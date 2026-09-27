"""Deterministic smoke coverage for RAM-only Presence camera vision."""

from __future__ import annotations

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PY_ROOT = PROJECT_ROOT.parent
if str(PY_ROOT) not in sys.path:
    sys.path.insert(0, str(PY_ROOT))


import cv2
import numpy as np

from nana.runtime.presence_camera_vision import (
    PresenceCameraVisionError,
    YuNetFacePresenceDetector,
)


def _blank_jpeg(width: int = 640, height: int = 480) -> bytes:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    encoded_ok, encoded = cv2.imencode(".jpg", image)
    assert encoded_ok
    return encoded.tobytes()


def main() -> None:
    detector = YuNetFacePresenceDetector()
    jpeg = _blank_jpeg()
    result = detector.detect_jpeg(
        jpeg,
        expected_width=640,
        expected_height=480,
    )
    assert result.frame_width == 640
    assert result.frame_height == 480
    assert result.face_count == 0
    assert not result.present

    try:
        detector.detect_jpeg(b"not-a-jpeg")
        raise AssertionError("malformed JPEG passed the vision boundary")
    except PresenceCameraVisionError as exc:
        assert "complete JPEG" in str(exc)

    try:
        detector.detect_jpeg(jpeg, expected_width=320, expected_height=480)
        raise AssertionError("dimension mismatch passed the vision boundary")
    except PresenceCameraVisionError as exc:
        assert "width mismatch" in str(exc)

    print(
        "smoke_presence_camera_vision: PASS | "
        "YuNet loaded | blank=absent | malformed=closed | dimensions=closed | "
        "persistence=none"
    )


if __name__ == "__main__":
    main()
