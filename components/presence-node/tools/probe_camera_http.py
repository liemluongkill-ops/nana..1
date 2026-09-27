from __future__ import annotations

import argparse
import json
import urllib.request
import webbrowser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / ".local" / "presence_node.json"
DEFAULT_CAPTURE = PROJECT_ROOT / "captures" / "nana_camera_latest.jpg"


def authenticated_get(url: str, token: str, timeout: float) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"X-Nana-Token": token, "Cache-Control": "no-cache"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe the Nana camera service and capture one requested frame."
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output", type=Path, default=DEFAULT_CAPTURE)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument(
        "--open", action="store_true", help="Open the live MJPEG viewer"
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text(encoding="utf-8"))
    base_url = str(config["base_url"]).rstrip("/")
    token = str(config["token"])

    health_data = authenticated_get(
        f"{base_url}/health", token, args.timeout
    )
    health = json.loads(health_data.decode("utf-8"))
    print(json.dumps(health, indent=2, ensure_ascii=True))

    jpeg = authenticated_get(
        f"{base_url}/capture.jpg", token, args.timeout
    )
    if len(jpeg) < 4 or jpeg[:2] != b"\xff\xd8" or jpeg[-2:] != b"\xff\xd9":
        raise RuntimeError("camera endpoint did not return a complete JPEG")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(jpeg)
    print(f"Capture: {args.output} ({len(jpeg)} bytes)")

    if args.open:
        webbrowser.open(str(config["view_url"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
