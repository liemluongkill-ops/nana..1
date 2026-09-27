#!/usr/bin/env python3
"""Detect faces in a Nana camera frame without assigning an identity."""

from __future__ import annotations

import argparse
from pathlib import Path

import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MODEL = PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Detect and annotate faces in an OV5640 capture."
    )
    parser.add_argument("image", type=Path, help="JPEG image captured by Nana")
    parser.add_argument(
        "--model",
        type=Path,
        default=DEFAULT_MODEL,
        help="YuNet ONNX model path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Annotated JPEG path (default: <image>_faces.jpg)",
    )
    parser.add_argument("--score-threshold", type=float, default=0.8)
    parser.add_argument(
        "--require-face",
        action="store_true",
        help="Return exit code 2 when no face is detected",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.model.is_file():
        raise SystemExit(f"YuNet model not found: {args.model}")
    if not args.image.is_file():
        raise SystemExit(f"Image not found: {args.image}")

    image = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"OpenCV could not decode: {args.image}")

    height, width = image.shape[:2]
    detector = cv2.FaceDetectorYN.create(
        str(args.model),
        "",
        (width, height),
        args.score_threshold,
        0.3,
        5000,
    )
    _, faces = detector.detect(image)
    face_count = 0 if faces is None else len(faces)

    if faces is not None:
        for index, face in enumerate(faces, start=1):
            x, y, box_width, box_height = face[:4].astype(int)
            score = float(face[-1])
            cv2.rectangle(
                image,
                (x, y),
                (x + box_width, y + box_height),
                (0, 220, 0),
                2,
            )
            cv2.putText(
                image,
                f"face {index}: {score:.3f}",
                (x, max(18, y - 8)),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (0, 220, 0),
                1,
                cv2.LINE_AA,
            )
            print(
                "face "
                f"{index}: score={score:.3f} "
                f"box=({x},{y},{box_width},{box_height})"
            )

    output = args.output or args.image.with_name(
        f"{args.image.stem}_faces.jpg"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(output), image):
        raise SystemExit(f"Could not write annotated image: {output}")

    print(
        "face_detection: "
        f"count={face_count} image={width}x{height} output={output}"
    )
    if args.require_face and face_count == 0:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
