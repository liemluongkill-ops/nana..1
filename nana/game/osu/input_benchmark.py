"""
osu! input benchmark / latency preview - Phase 23.

Read-only timing probe for future osu! input work.

No live input. No cursor move/click. No SetCursorPos. No SendInput.
No mouse_event. No score submit. No VTS. No ElevenLabs. No OBS API.
No Vision. No Stardew changes. No external bot import/run.
"""

from __future__ import annotations

import math
import time
from typing import Any, Callable

from nana.game.osu.input_readiness import (
    DEFAULT_LEASE_LIMIT,
    DEFAULT_READINESS_STALE_MS,
    build_input_readiness_status,
)
from nana.game.osu.router import DEFAULT_AIM_LEAD_MS


DEFAULT_BENCHMARK_SAMPLES = 10
DEFAULT_BENCHMARK_INTERVAL_MS = 25

ReadinessBuilder = Callable[..., dict[str, Any]]
SleepFn = Callable[[float], None]

_LAST_BENCHMARK_PREVIEW: dict[str, Any] | None = None


def _is_truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_benchmark_flags(text: str) -> dict[str, Any]:
    values: dict[str, Any] = {
        "samples": DEFAULT_BENCHMARK_SAMPLES,
        "interval_ms": DEFAULT_BENCHMARK_INTERVAL_MS,
        "lead_ms": DEFAULT_AIM_LEAD_MS,
        "limit": DEFAULT_LEASE_LIMIT,
        "stale_ms": DEFAULT_READINESS_STALE_MS,
    }
    for token in str(text or "").split()[1:]:
        if not token.startswith("--") or "=" not in token:
            continue
        name, raw_value = token[2:].split("=", 1)
        if name == "samples":
            values["samples"] = int(raw_value)
        elif name == "interval-ms":
            values["interval_ms"] = int(raw_value)
        elif name == "lead-ms":
            values["lead_ms"] = int(raw_value)
        elif name == "limit":
            values["limit"] = int(raw_value)
        elif name == "stale-ms":
            values["stale_ms"] = int(raw_value)
        elif name in {"adapter-enabled", "zone-enabled", "osu-zone-enabled"}:
            values["adapter_enabled"] = _is_truthy(raw_value)
    if int(values["samples"]) <= 0:
        raise ValueError("samples_must_be_positive")
    if int(values["interval_ms"]) < 0:
        raise ValueError("interval_ms_must_be_non_negative")
    if int(values["lead_ms"]) < 0:
        raise ValueError("lead_ms_must_be_non_negative")
    if int(values["limit"]) <= 0:
        raise ValueError("limit_must_be_positive")
    if int(values["stale_ms"]) <= 0:
        raise ValueError("stale_ms_must_be_positive")
    return values


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil((percentile / 100.0) * len(ordered)) - 1))
    return ordered[index]


def _latency_stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min_ms": None, "median_ms": None, "max_ms": None, "p95_ms": None}
    ordered = sorted(values)
    midpoint = len(ordered) // 2
    if len(ordered) % 2:
        median = ordered[midpoint]
    else:
        median = (ordered[midpoint - 1] + ordered[midpoint]) / 2.0
    return {
        "min_ms": round(ordered[0], 3),
        "median_ms": round(median, 3),
        "max_ms": round(ordered[-1], 3),
        "p95_ms": round(_percentile(ordered, 95) or 0.0, 3),
    }


def _recommended_benchmark(status: dict[str, Any]) -> str:
    preview_ready = (
        bool(status.get("adapter_zone_enabled"))
        and bool(status.get("bridge_ok"))
        and bool(status.get("map_loaded"))
        and bool(status.get("object_stream_available"))
        and bool(status.get("playfield_calibrated"))
    )
    benchmark_ready = (
        preview_ready
        and status.get("activity_state") == "gameplay"
        and status.get("current_time_ms") is not None
    )
    if benchmark_ready and status.get("readiness") == "ready":
        return "ready_for_future_input_benchmark"
    if preview_ready:
        return "benchmark_preview_only"
    return "not_ready"


def _safe_current_time(status: dict[str, Any]) -> int | None:
    value = status.get("current_time_ms")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _build_sample(
    text: str,
    *,
    readiness_builder: ReadinessBuilder,
    readiness_kwargs: dict[str, Any],
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        status = readiness_builder(text, **readiness_kwargs)
        error = None
    except Exception as exc:
        status = {
            "decision": "input_readiness_status_only_no_input",
            "readiness": "not_ready",
            "blockers": [f"benchmark_readiness_error:{type(exc).__name__}"],
            "current_time_ms": None,
            "activity_state": "unknown",
            "adapter_zone_enabled": False,
            "bridge_ok": False,
            "map_loaded": False,
            "object_stream_available": False,
            "playfield_calibrated": False,
            "timing_budget": {
                "lead_ms": None,
                "limit": None,
                "preview_count": 0,
                "preview_cost_ms": None,
            },
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "obs_call": False,
            "vision_call": False,
        }
        error = f"{type(exc).__name__}:{exc}"
    loop_ms = round((time.perf_counter() - started) * 1000.0, 3)
    timing = status.get("timing_budget") or {}
    preview_cost = timing.get("preview_cost_ms")
    try:
        preview_cost = round(float(preview_cost), 3) if preview_cost is not None else None
    except (TypeError, ValueError):
        preview_cost = None
    return {
        "status": status,
        "loop_latency_ms": loop_ms,
        "preview_cost_ms": preview_cost,
        "current_time_ms": _safe_current_time(status),
        "readiness": status.get("readiness") or "not_ready",
        "blockers": list(status.get("blockers") or []),
        "error": error,
        "ok": error is None,
    }


def build_input_benchmark_preview(
    text: str = "",
    *,
    readiness_builder: ReadinessBuilder | None = None,
    sleep_fn: SleepFn | None = None,
    **readiness_kwargs: Any,
) -> dict[str, Any]:
    """
    Build a read-only benchmark preview.

    The command path reads only existing tosu/map/calibration data through
    Phase 22 readiness. It never moves/clicks, submits, or calls external APIs.
    """
    flags = _parse_benchmark_flags(text)
    samples_requested = int(flags["samples"])
    interval_ms = int(flags["interval_ms"])
    lead_ms = int(flags["lead_ms"])
    limit = int(flags["limit"])
    readiness_builder = readiness_builder or build_input_readiness_status
    sleep_fn = sleep_fn or time.sleep

    initial_sample = _build_sample(
        text,
        readiness_builder=readiness_builder,
        readiness_kwargs=readiness_kwargs,
    )
    initial_status = initial_sample["status"]
    recommended = _recommended_benchmark(initial_status)
    initial_readiness = initial_status.get("readiness") or "not_ready"
    blockers = list(initial_status.get("blockers") or [])
    initial_time = initial_sample.get("current_time_ms")

    if recommended != "ready_for_future_input_benchmark":
        decision = (
            "input_benchmark_preview_preview_only_hold_no_input"
            if recommended == "benchmark_preview_only"
            else "input_benchmark_preview_not_ready_hold_no_input"
        )
        result = {
            "decision": decision,
            "readiness": initial_readiness,
            "recommended_benchmark": recommended,
            "samples_requested": samples_requested,
            "samples_ok": 0,
            "samples_ready": 0,
            "samples_attempted": 0,
            "loop_latency": _latency_stats([]),
            "preview_cost": _latency_stats([]),
            "initial_loop_latency_ms": initial_sample["loop_latency_ms"],
            "initial_preview_cost_ms": initial_sample["preview_cost_ms"],
            "current_time_first": initial_time,
            "current_time_last": initial_time,
            "current_time_delta": 0 if initial_time is not None else None,
            "timing_lead_ms": lead_ms,
            "timing_limit": limit,
            "blockers": blockers,
            "initial_status": initial_status,
            "sample_errors": [initial_sample["error"]] if initial_sample["error"] else [],
            "real_input_allowed": False,
            "submit_allowed": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "voice_call": False,
            "obs_call": False,
            "vision_call": False,
            "created_at": time.time(),
        }
        remember_input_benchmark_preview(result)
        return result

    samples: list[dict[str, Any]] = []
    for index in range(samples_requested):
        if index > 0 and interval_ms > 0:
            sleep_fn(interval_ms / 1000.0)
        samples.append(
            _build_sample(
                text,
                readiness_builder=readiness_builder,
                readiness_kwargs=readiness_kwargs,
            )
        )

    ok_samples = [sample for sample in samples if sample["ok"]]
    ready_samples = [sample for sample in ok_samples if sample["readiness"] == "ready"]
    loop_values = [float(sample["loop_latency_ms"]) for sample in ok_samples]
    preview_values = [
        float(sample["preview_cost_ms"])
        for sample in ok_samples
        if sample.get("preview_cost_ms") is not None
    ]
    current_times = [
        int(sample["current_time_ms"])
        for sample in ok_samples
        if sample.get("current_time_ms") is not None
    ]
    if len(ready_samples) == samples_requested:
        decision = "input_benchmark_preview_ready_for_future_input_benchmark_no_input"
        readiness = "ready"
    else:
        decision = "input_benchmark_preview_unstable_hold_no_input"
        readiness = "unstable"
        recommended = "benchmark_preview_only"

    sample_blockers: list[str] = []
    for sample in samples:
        for blocker in sample.get("blockers") or []:
            if blocker not in sample_blockers:
                sample_blockers.append(blocker)

    result = {
        "decision": decision,
        "readiness": readiness,
        "recommended_benchmark": recommended,
        "samples_requested": samples_requested,
        "samples_ok": len(ok_samples),
        "samples_ready": len(ready_samples),
        "samples_attempted": len(samples),
        "loop_latency": _latency_stats(loop_values),
        "preview_cost": _latency_stats(preview_values),
        "initial_loop_latency_ms": initial_sample["loop_latency_ms"],
        "initial_preview_cost_ms": initial_sample["preview_cost_ms"],
        "current_time_first": current_times[0] if current_times else initial_time,
        "current_time_last": current_times[-1] if current_times else initial_time,
        "current_time_delta": (
            current_times[-1] - current_times[0]
            if len(current_times) >= 2
            else (0 if current_times else None)
        ),
        "timing_lead_ms": lead_ms,
        "timing_limit": limit,
        "blockers": sample_blockers,
        "initial_status": initial_status,
        "sample_errors": [sample["error"] for sample in samples if sample["error"]],
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
        "created_at": time.time(),
    }
    remember_input_benchmark_preview(result)
    return result


def remember_input_benchmark_preview(preview: dict[str, Any]) -> None:
    global _LAST_BENCHMARK_PREVIEW
    _LAST_BENCHMARK_PREVIEW = dict(preview)


def build_input_benchmark_status() -> dict[str, Any]:
    if _LAST_BENCHMARK_PREVIEW is None:
        return {
            "decision": "input_benchmark_status_no_cached_preview",
            "cached": False,
            "message": "No cached benchmark preview in this process. Run /osu-input-benchmark-preview first.",
            "last_preview": None,
            "real_input_allowed": False,
            "submit_allowed": False,
            "real_input": False,
            "submit": False,
            "vts_call": False,
            "voice_call": False,
            "obs_call": False,
            "vision_call": False,
        }
    return {
        "decision": "input_benchmark_status_cached_preview",
        "cached": True,
        "message": "Cached in-process benchmark preview.",
        "last_preview": dict(_LAST_BENCHMARK_PREVIEW),
        "real_input_allowed": False,
        "submit_allowed": False,
        "real_input": False,
        "submit": False,
        "vts_call": False,
        "voice_call": False,
        "obs_call": False,
        "vision_call": False,
    }


def _fmt_stats(stats: dict[str, Any]) -> str:
    return (
        f"min={stats.get('min_ms')} median={stats.get('median_ms')} "
        f"max={stats.get('max_ms')} p95={stats.get('p95_ms')}"
    )


def print_input_benchmark_preview(preview: dict[str, Any]) -> None:
    print("osu! Input Benchmark Preview")
    print(f"  Decision: {preview['decision']}")
    print(f"  Readiness: {preview['readiness']}")
    print(f"  Recommended benchmark: {preview['recommended_benchmark']}")
    print(f"  Samples requested: {preview['samples_requested']}")
    print(f"  Samples attempted: {preview['samples_attempted']}")
    print(f"  Samples ok: {preview['samples_ok']}")
    print(f"  Samples ready: {preview['samples_ready']}")
    print(f"  Loop latency ms: {_fmt_stats(preview['loop_latency'])}")
    print(f"  Preview cost ms: {_fmt_stats(preview['preview_cost'])}")
    print(f"  Initial loop latency ms: {preview['initial_loop_latency_ms']}")
    print(f"  Initial preview cost ms: {preview['initial_preview_cost_ms']}")
    print(f"  Current time first: {preview['current_time_first']}")
    print(f"  Current time last: {preview['current_time_last']}")
    print(f"  Current time delta: {preview['current_time_delta']}")
    print(f"  Timing lead ms: {preview['timing_lead_ms']}")
    print(f"  Timing limit: {preview['timing_limit']}")
    print(f"  Blockers: {preview['blockers']}")
    if preview.get("sample_errors"):
        print(f"  Sample errors: {preview['sample_errors']}")
    print(f"  Real input allowed: {preview['real_input_allowed']}")
    print(f"  Submit allowed: {preview['submit_allowed']}")
    print(f"  Real input: {preview['real_input']}")
    print(f"  Submit: {preview['submit']}")
    print(f"  VTS call: {preview['vts_call']}")
    print(f"  Voice call: {preview['voice_call']}")
    print(f"  OBS call: {preview['obs_call']}")
    print(f"  Vision call: {preview['vision_call']}")


def print_input_benchmark_status(status: dict[str, Any]) -> None:
    print("osu! Input Benchmark Status")
    print(f"  Decision: {status['decision']}")
    print(f"  Cached: {status['cached']}")
    print(f"  Message: {status['message']}")
    preview = status.get("last_preview")
    if preview:
        print(f"  Last decision: {preview['decision']}")
        print(f"  Last readiness: {preview['readiness']}")
        print(f"  Last recommended benchmark: {preview['recommended_benchmark']}")
        print(f"  Last samples ok: {preview['samples_ok']}")
        print(f"  Last loop latency ms: {_fmt_stats(preview['loop_latency'])}")
        print(f"  Last preview cost ms: {_fmt_stats(preview['preview_cost'])}")
        print(f"  Last current time delta: {preview['current_time_delta']}")
        print(f"  Last blockers: {preview['blockers']}")
    print(f"  Real input allowed: {status['real_input_allowed']}")
    print(f"  Submit allowed: {status['submit_allowed']}")
    print(f"  Real input: {status['real_input']}")
    print(f"  Submit: {status['submit']}")
    print(f"  VTS call: {status['vts_call']}")
    print(f"  Voice call: {status['voice_call']}")
    print(f"  OBS call: {status['obs_call']}")
    print(f"  Vision call: {status['vision_call']}")
