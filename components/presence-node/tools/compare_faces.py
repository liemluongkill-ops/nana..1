#!/usr/bin/env python3
"""Compare two face images with YuNet detection and SFace embeddings."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import cv2


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DETECTOR = PROJECT_ROOT / "models" / "face_detection_yunet_2023mar.onnx"
DEFAULT_RECOGNIZER = (
    PROJECT_ROOT / "models" / "face_recognition_sface_2021dec.onnx"
)
DEFAULT_COSINE_THRESHOLD = 0.363
DEFAULT_L2_THRESHOLD = 1.128


@dataclass(frozen=True)
class FaceSample:
    path: Path
    face_count: int
    score: float
    box: tuple[int, int, int, int]
    aligned: object
    feature: object
    sharpness: float


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare the strongest detected face in two images."
    )
    parser.add_argument("reference", type=Path, help="Registered/reference image")
    parser.add_argument("candidate", type=Path, help="Candidate image")
    parser.add_argument(
        "--detector",
        type=Path,
        default=DEFAULT_DETECTOR,
        help="YuNet ONNX model path",
    )
    parser.add_argument(
        "--recognizer",
        type=Path,
        default=DEFAULT_RECOGNIZER,
        help="SFace ONNX model path",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Optional side-by-side image of the aligned 112x112 face crops",
    )
    parser.add_argument("--score-threshold", type=float, default=0.8)
    parser.add_argument(
        "--cosine-threshold", type=float, default=DEFAULT_COSINE_THRESHOLD
    )
    parser.add_argument("--l2-threshold", type=float, default=DEFAULT_L2_THRESHOLD)
    return parser.parse_args()


def require_file(path: Path, label: str) -> None:
    if not path.is_file():
        raise SystemExit(f"{label} not found: {path}")


def read_image(path: Path) -> object:
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise SystemExit(f"OpenCV could not decode: {path}")
    return image


def extract_sample(
    path: Path,
    detector: object,
    recognizer: object,
) -> FaceSample:
    image = read_image(path)
    height, width = image.shape[:2]
    detector.setInputSize((width, height))
    _, faces = detector.detect(image)
    if faces is None or len(faces) == 0:
        raise SystemExit(f"No face detected: {path}")

    # Prefer confidence first and face area second when a test image has several faces.
    face = max(
        faces,
        key=lambda item: (float(item[-1]), float(item[2] * item[3])),
    )
    x, y, box_width, box_height = face[:4].astype(int)
    aligned = recognizer.alignCrop(image, face)
    feature = recognizer.feature(aligned)
    gray = cv2.cvtColor(aligned, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    return FaceSample(
        path=path,
        face_count=len(faces),
        score=float(face[-1]),
        box=(x, y, box_width, box_height),
        aligned=aligned,
        feature=feature,
        sharpness=sharpness,
    )


def print_sample(label: str, sample: FaceSample) -> None:
    x, y, width, height = sample.box
    print(
        f"{label}: faces={sample.face_count} score={sample.score:.3f} "
        f"box=({x},{y},{width},{height}) aligned=112x112 "
        f"sharpness={sample.sharpness:.1f} path={sample.path}"
    )


def write_aligned_pair(
    path: Path,
    reference: FaceSample,
    candidate: FaceSample,
    cosine: float,
    l2_distance: float,
) -> None:
    pair = cv2.hconcat([reference.aligned, candidate.aligned])
    footer = cv2.copyMakeBorder(
        pair,
        0,
        42,
        0,
        0,
        cv2.BORDER_CONSTANT,
        value=(20, 20, 20),
    )
    cv2.putText(
        footer,
        f"cos={cosine:.3f} l2={l2_distance:.3f}",
        (8, pair.shape[0] + 27),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (0, 220, 220),
        1,
        cv2.LINE_AA,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), footer):
        raise SystemExit(f"Could not write aligned comparison: {path}")


def main() -> int:
    args = parse_args()
    require_file(args.reference, "Reference image")
    require_file(args.candidate, "Candidate image")
    require_file(args.detector, "YuNet model")
    require_file(args.recognizer, "SFace model")

    detector = cv2.FaceDetectorYN.create(
        str(args.detector),
        "",
        (320, 320),
        args.score_threshold,
        0.3,
        5000,
    )
    recognizer = cv2.FaceRecognizerSF.create(str(args.recognizer), "")
    reference = extract_sample(args.reference, detector, recognizer)
    candidate = extract_sample(args.candidate, detector, recognizer)

    cosine = float(
        recognizer.match(
            reference.feature,
            candidate.feature,
            cv2.FaceRecognizerSF_FR_COSINE,
        )
    )
    l2_distance = float(
        recognizer.match(
            reference.feature,
            candidate.feature,
            cv2.FaceRecognizerSF_FR_NORM_L2,
        )
    )
    cosine_pass = cosine >= args.cosine_threshold
    l2_pass = l2_distance <= args.l2_threshold
    same_identity_smoke = cosine_pass and l2_pass

    print_sample("reference", reference)
    print_sample("candidate", candidate)
    print(
        "similarity: "
        f"cosine={cosine:.4f} threshold>={args.cosine_threshold:.3f} "
        f"pass={cosine_pass} | l2={l2_distance:.4f} "
        f"threshold<={args.l2_threshold:.3f} pass={l2_pass}"
    )
    print(f"same_identity_smoke={same_identity_smoke}")

    if args.output:
        write_aligned_pair(args.output, reference, candidate, cosine, l2_distance)
        print(f"aligned_output={args.output}")
    return 0 if same_identity_smoke else 3


if __name__ == "__main__":
    raise SystemExit(main())
