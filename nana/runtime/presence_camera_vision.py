"""RAM-only face-presence detection for Presence camera snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time


DEFAULT_YUNET_MODEL_PATH = (
    Path(__file__).resolve().parents[1]
    / "assets"
    / "models"
    / "face_detection_yunet_2023mar.onnx"
)
DEFAULT_SCORE_THRESHOLD = 0.80
DEFAULT_NMS_THRESHOLD = 0.30
DEFAULT_TOP_K = 5000


class PresenceCameraVisionError(RuntimeError):
    """Raised when an in-memory camera frame cannot be evaluated."""


@dataclass(frozen=True)
class PresenceFace:
    x: int
    y: int
    width: int
    height: int
    confidence: float


@dataclass(frozen=True)
class FacePresenceResult:
    frame_width: int
    frame_height: int
    faces: tuple[PresenceFace, ...]
    inference_ms: float

    @property
    def present(self) -> bool:
        return bool(self.faces)

    @property
    def face_count(self) -> int:
        return len(self.faces)

    @property
    def max_confidence(self) -> float:
        return max((face.confidence for face in self.faces), default=0.0)


class YuNetFacePresenceDetector:
    """Decode a JPEG in memory and evaluate face presence with OpenCV YuNet."""

    def __init__(
        self,
        model_path: str | Path = DEFAULT_YUNET_MODEL_PATH,
        *,
        score_threshold: float = DEFAULT_SCORE_THRESHOLD,
        nms_threshold: float = DEFAULT_NMS_THRESHOLD,
        top_k: int = DEFAULT_TOP_K,
    ) -> None:
        self.model_path = Path(model_path)
        self.score_threshold = float(score_threshold)
        self.nms_threshold = float(nms_threshold)
        self.top_k = int(top_k)
        if not 0.0 < self.score_threshold <= 1.0:
            raise ValueError("score_threshold must be in (0, 1]")
        if not 0.0 < self.nms_threshold <= 1.0:
            raise ValueError("nms_threshold must be in (0, 1]")
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        self._detector = None
        self._input_size: tuple[int, int] | None = None
        self._lock = threading.Lock()

    def _ensure_detector(self, width: int, height: int):
        try:
            import cv2
        except ImportError as exc:
            raise PresenceCameraVisionError(
                "OpenCV is required for Presence face detection"
            ) from exc
        if not self.model_path.is_file():
            raise PresenceCameraVisionError(
                f"YuNet model is missing: {self.model_path}"
            )
        input_size = (int(width), int(height))
        if self._detector is None:
            self._detector = cv2.FaceDetectorYN.create(
                str(self.model_path),
                "",
                input_size,
                self.score_threshold,
                self.nms_threshold,
                self.top_k,
            )
            self._input_size = input_size
        elif self._input_size != input_size:
            self._detector.setInputSize(input_size)
            self._input_size = input_size
        return self._detector

    def detect_jpeg(
        self,
        jpeg: bytes | bytearray | memoryview,
        *,
        expected_width: int | None = None,
        expected_height: int | None = None,
    ) -> FacePresenceResult:
        payload = bytes(jpeg)
        if len(payload) < 4 or payload[:2] != b"\xff\xd8" or payload[-2:] != b"\xff\xd9":
            raise PresenceCameraVisionError("camera payload is not a complete JPEG")

        try:
            import cv2
            import numpy as np
        except ImportError as exc:
            raise PresenceCameraVisionError(
                "OpenCV and NumPy are required for Presence face detection"
            ) from exc

        encoded = np.frombuffer(payload, dtype=np.uint8)
        image = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
        if image is None or image.ndim != 3:
            raise PresenceCameraVisionError("OpenCV could not decode the camera JPEG")
        height, width = image.shape[:2]
        if expected_width is not None and width != int(expected_width):
            raise PresenceCameraVisionError(
                f"decoded width mismatch: expected {expected_width}, got {width}"
            )
        if expected_height is not None and height != int(expected_height):
            raise PresenceCameraVisionError(
                f"decoded height mismatch: expected {expected_height}, got {height}"
            )

        started_at = time.perf_counter()
        with self._lock:
            detector = self._ensure_detector(width, height)
            _retval, raw_faces = detector.detect(image)
        inference_ms = (time.perf_counter() - started_at) * 1000.0

        faces: list[PresenceFace] = []
        if raw_faces is not None:
            for row in raw_faces:
                x, y, face_width, face_height = (int(round(float(value))) for value in row[:4])
                confidence = float(row[-1])
                faces.append(
                    PresenceFace(
                        x=max(0, x),
                        y=max(0, y),
                        width=max(0, face_width),
                        height=max(0, face_height),
                        confidence=confidence,
                    )
                )

        return FacePresenceResult(
            frame_width=width,
            frame_height=height,
            faces=tuple(faces),
            inference_ms=inference_ms,
        )


__all__ = [
    "DEFAULT_YUNET_MODEL_PATH",
    "FacePresenceResult",
    "PresenceCameraVisionError",
    "PresenceFace",
    "YuNetFacePresenceDetector",
]
