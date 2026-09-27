from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


DEFAULT_STALE_MS = 3000


def _build_status_payload(
    *,
    source: Path,
    status: str,
    reason: str,
    calibrated: bool,
    count: int,
    immediate: int,
    upcoming: int,
    max_speed_px_s: float | None,
) -> dict[str, Any]:
    return {
        "decision": "status_only_no_input",
        "source": str(source),
        "status": status,
        "calibrated": calibrated,
        "count": count,
        "immediate": immediate,
        "upcoming": upcoming,
        "max_speed_px_s": max_speed_px_s,
        "reason": reason,
        "real_input": False,
        "submit": False,
    }


def read_aim_status(path: Path, *, stale_ms: int = DEFAULT_STALE_MS) -> dict[str, Any]:
    if stale_ms <= 0:
        raise ValueError("stale_ms_must_be_positive")
    if not path.exists():
        return _build_status_payload(
            source=path,
            status="unavailable",
            reason="timeline file missing",
            calibrated=False,
            count=0,
            immediate=0,
            upcoming=0,
            max_speed_px_s=None,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return _build_status_payload(
            source=path,
            status="unavailable",
            reason=f"invalid timeline json: {exc}",
            calibrated=False,
            count=0,
            immediate=0,
            upcoming=0,
            max_speed_px_s=None,
        )

    calibrated = bool(payload.get("calibrated", False))
    objects = payload.get("objects") or []
    reported_count = int(payload.get("count", len(objects)) or 0)
    windows = payload.get("windows") or {}
    immediate = int(windows.get("immediate_0_500_ms") or 0)
    upcoming = int(windows.get("upcoming_500_1500_ms") or 0)
    speeds = [
        float(speed)
        for speed in (obj.get("aim_speed_px_s") for obj in objects if isinstance(obj, dict))
        if speed is not None
    ]
    max_speed_px_s = round(max(speeds), 2) if speeds else None
    written_at = payload.get("written_at")
    age_ms = None
    if written_at is not None:
        try:
            age_ms = max(0, int((time.time() - float(written_at)) * 1000))
        except (TypeError, ValueError):
            age_ms = None

    if not calibrated:
        return _build_status_payload(
            source=path,
            status="uncalibrated",
            reason="timeline has no screen calibration",
            calibrated=calibrated,
            count=reported_count,
            immediate=immediate,
            upcoming=upcoming,
            max_speed_px_s=max_speed_px_s,
        )
    if age_ms is None:
        return _build_status_payload(
            source=path,
            status="unavailable",
            reason="timeline missing written_at",
            calibrated=calibrated,
            count=reported_count,
            immediate=immediate,
            upcoming=upcoming,
            max_speed_px_s=max_speed_px_s,
        )
    if age_ms > stale_ms:
        return _build_status_payload(
            source=path,
            status="stale",
            reason=f"timeline age {age_ms}ms exceeds {stale_ms}ms",
            calibrated=calibrated,
            count=reported_count,
            immediate=immediate,
            upcoming=upcoming,
            max_speed_px_s=max_speed_px_s,
        )
    if not objects:
        return _build_status_payload(
            source=path,
            status="idle",
            reason="timeline has no upcoming objects",
            calibrated=calibrated,
            count=0,
            immediate=immediate,
            upcoming=upcoming,
            max_speed_px_s=max_speed_px_s,
        )
    if immediate >= 3:
        return _build_status_payload(
            source=path,
            status="dense_pattern",
            reason=f"{immediate} objects in immediate window",
            calibrated=calibrated,
            count=reported_count,
            immediate=immediate,
            upcoming=upcoming,
            max_speed_px_s=max_speed_px_s,
        )
    if max_speed_px_s is not None and max_speed_px_s >= 1000:
        return _build_status_payload(
            source=path,
            status="fast_jump",
            reason=f"max speed {max_speed_px_s} px/s",
            calibrated=calibrated,
            count=reported_count,
            immediate=immediate,
            upcoming=upcoming,
            max_speed_px_s=max_speed_px_s,
        )
    return _build_status_payload(
        source=path,
        status="tracking",
        reason="timeline active with calibrated targets",
        calibrated=calibrated,
        count=reported_count,
        immediate=immediate,
        upcoming=upcoming,
        max_speed_px_s=max_speed_px_s,
    )
