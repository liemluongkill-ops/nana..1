from __future__ import annotations

import hashlib
from pathlib import Path
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "nana" / "assets" / "models" / "face_detection_yunet_2023mar.onnx"
URL = (
    "https://media.githubusercontent.com/media/opencv/opencv_zoo/"
    "f12e12798e8314f7c074a6656816c048dcc95b7a/"
    "models/face_detection_yunet/face_detection_yunet_2023mar.onnx"
)
EXPECTED_SHA256 = "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"


def main() -> int:
    if TARGET.exists() and hashlib.sha256(TARGET.read_bytes()).hexdigest() == EXPECTED_SHA256:
        print(f"Already verified: {TARGET.relative_to(ROOT)}")
        return 0

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    temporary = TARGET.with_suffix(TARGET.suffix + ".download")
    try:
        urllib.request.urlretrieve(URL, temporary)
        digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
        if digest != EXPECTED_SHA256:
            raise RuntimeError(f"YuNet checksum mismatch: {digest}")
        temporary.replace(TARGET)
    finally:
        temporary.unlink(missing_ok=True)
    print(f"Downloaded and verified: {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
