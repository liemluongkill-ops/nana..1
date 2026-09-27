from __future__ import annotations

import argparse
import base64
import getpass
import json
import sys
import time
import urllib.parse
import webbrowser
from pathlib import Path

import serial
from serial.tools import list_ports


PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG = PROJECT_ROOT / ".local" / "presence_node.json"
CH343_VID = 0x1A86
CH343_PID = 0x55D3


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


def encode_field(value: str) -> str:
    if not value:
        return "-"
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def build_set_command(ssid: str, password: str) -> bytes:
    return (
        f"NANA_WIFI_SET {encode_field(ssid)} {encode_field(password)}\n"
    ).encode("ascii")


def parse_camera_view(line: str) -> tuple[str, str] | None:
    prefix = "NANA_CAMERA_VIEW="
    if not line.startswith(prefix):
        return None
    view_url = line[len(prefix) :].strip()
    parsed = urllib.parse.urlparse(view_url)
    token = urllib.parse.parse_qs(parsed.query).get("token", [""])[0]
    if not parsed.scheme or not parsed.netloc or not token:
        return None
    base_url = urllib.parse.urlunparse(
        (parsed.scheme, parsed.netloc, "", "", "", "")
    )
    return base_url, token


def save_local_config(
    *, base_url: str, token: str, view_url: str, ssid: str
) -> None:
    LOCAL_CONFIG.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "base_url": base_url,
        "token": token,
        "view_url": view_url,
        "ssid": ssid,
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    LOCAL_CONFIG.write_text(
        json.dumps(payload, indent=2, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )


def serial_session(
    *, port: str, command: bytes, ssid: str, timeout: float, open_view: bool
) -> int:
    deadline = time.monotonic() + timeout
    next_send = 0.0
    command_confirmed = False

    with serial.Serial(port, 115200, timeout=0.25) as connection:
        connection.dtr = False
        connection.rts = False
        time.sleep(0.4)
        connection.reset_input_buffer()

        while time.monotonic() < deadline:
            now = time.monotonic()
            if not command_confirmed and now >= next_send:
                connection.write(command)
                connection.flush()
                next_send = now + 2.0

            raw_line = connection.readline()
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue

            if "NANA_WIFI_PROVISION_READY" in line and not command_confirmed:
                connection.write(command)
                connection.flush()
                next_send = now + 2.0
                continue
            if "NANA_WIFI_SAVED" in line or "NANA_WIFI_UNCHANGED" in line:
                command_confirmed = True
                print(line)
                continue
            if "NANA_WIFI_CLEARED" in line:
                print(line)
                return 0

            camera_view = parse_camera_view(line)
            if camera_view is None:
                if "nana_wifi" in line or "NANA_CAMERA" in line:
                    print(line)
                continue

            base_url, token = camera_view
            view_url = line.split("=", 1)[1]
            save_local_config(
                base_url=base_url,
                token=token,
                view_url=view_url,
                ssid=ssid,
            )
            print(f"Camera ready: {view_url}")
            print(f"Local config: {LOCAL_CONFIG}")
            if open_view:
                webbrowser.open(view_url)
            return 0

    if command_confirmed:
        print(
            "Wi-Fi credentials were saved, but the camera URL did not appear. "
            "Check SSID/password and the serial log.",
            file=sys.stderr,
        )
    else:
        print(
            "The board did not acknowledge provisioning. Flash the LAN firmware "
            "and verify the USB-UART port.",
            file=sys.stderr,
        )
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Provision Nana Presence Node Wi-Fi over USB-UART."
    )
    parser.add_argument("--port", help="CH343 serial port; auto-detected by default")
    parser.add_argument("--ssid", help="Wi-Fi SSID; prompted when omitted")
    parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read one password line from stdin instead of prompting",
    )
    parser.add_argument(
        "--timeout", type=float, default=75.0, help="Overall wait in seconds"
    )
    parser.add_argument(
        "--no-open", action="store_true", help="Do not open the camera viewer"
    )
    parser.add_argument(
        "--clear", action="store_true", help="Erase saved Wi-Fi credentials"
    )
    args = parser.parse_args()

    try:
        port = args.port or find_ch343_port()
    except RuntimeError as error:
        parser.error(str(error))

    if args.clear:
        return serial_session(
            port=port,
            command=b"NANA_WIFI_CLEAR\n",
            ssid="",
            timeout=args.timeout,
            open_view=False,
        )

    ssid = (args.ssid or input("Wi-Fi SSID: ")).strip()
    if not ssid:
        parser.error("SSID cannot be empty")
    if len(ssid.encode("utf-8")) > 32:
        parser.error("SSID must be at most 32 UTF-8 bytes")

    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
        if not password:
            parser.error("password stdin was empty")
    else:
        password = getpass.getpass("Wi-Fi password (hidden): ")
    if len(password.encode("utf-8")) > 64:
        parser.error("Password must be at most 64 UTF-8 bytes")

    print(f"Provisioning {port}; the password is not written to disk.")
    return serial_session(
        port=port,
        command=build_set_command(ssid, password),
        ssid=ssid,
        timeout=args.timeout,
        open_view=not args.no_open,
    )


if __name__ == "__main__":
    raise SystemExit(main())
