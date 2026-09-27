from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class HitObjectType:
    bit: int
    name: str


HIT_OBJECT_TYPES = (
    HitObjectType(1, "circle"),
    HitObjectType(2, "slider"),
    HitObjectType(8, "spinner"),
    HitObjectType(128, "hold"),
)


def _parse_scalar(value: str) -> Any:
    text = str(value).strip()
    if text == "":
        return ""
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        return text


def _parse_key_value(line: str) -> tuple[str, Any] | None:
    if ":" not in line:
        return None
    key, value = line.split(":", 1)
    return key.strip(), _parse_scalar(value)


def _hit_object_kind(type_value: int) -> str:
    for object_type in HIT_OBJECT_TYPES:
        if type_value & object_type.bit:
            return object_type.name
    return "unknown"


def _parse_timing_point(line: str) -> dict[str, Any] | None:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 2:
        return None
    return {
        "time": _parse_scalar(parts[0]),
        "beat_length": _parse_scalar(parts[1]),
        "meter": _parse_scalar(parts[2]) if len(parts) > 2 else None,
        "sample_set": _parse_scalar(parts[3]) if len(parts) > 3 else None,
        "sample_index": _parse_scalar(parts[4]) if len(parts) > 4 else None,
        "volume": _parse_scalar(parts[5]) if len(parts) > 5 else None,
        "uninherited": _parse_scalar(parts[6]) if len(parts) > 6 else None,
        "effects": _parse_scalar(parts[7]) if len(parts) > 7 else None,
    }


def _parse_hit_object(line: str, index: int) -> dict[str, Any] | None:
    parts = [part.strip() for part in line.split(",")]
    if len(parts) < 5:
        return None
    try:
        x = int(parts[0])
        y = int(parts[1])
        time_ms = int(parts[2])
        type_value = int(parts[3])
    except ValueError:
        return None

    kind = _hit_object_kind(type_value)
    params = parts[5:] if len(parts) > 5 else []
    end_time = None
    slider = None
    spinner = None
    hold = None

    if kind == "slider" and params:
        slider_parts = params[0].split("|")
        slider = {
            "curve": slider_parts[0] if slider_parts else "",
            "points": slider_parts[1:],
            "slides": _parse_scalar(params[1]) if len(params) > 1 else None,
            "length": _parse_scalar(params[2]) if len(params) > 2 else None,
        }
    elif kind == "spinner" and params:
        end_time = int(_parse_scalar(params[0]))
        spinner = {"end_time": end_time}
    elif kind == "hold" and params:
        first_param = str(params[0])
        end_text = first_param.split(":", 1)[0]
        try:
            end_time = int(end_text)
        except ValueError:
            end_time = None
        hold = {"end_time": end_time}

    return {
        "index": index,
        "x": x,
        "y": y,
        "time": time_ms,
        "type": type_value,
        "kind": kind,
        "hit_sound": _parse_scalar(parts[4]),
        "raw": line,
        "params": params,
        "end_time": end_time,
        "slider": slider,
        "spinner": spinner,
        "hold": hold,
    }


def parse_osu_text(text: str, *, source: str | None = None) -> dict[str, Any]:
    section = "File"
    sections: dict[str, list[str]] = {}
    metadata: dict[str, Any] = {}
    difficulty: dict[str, Any] = {}
    general: dict[str, Any] = {}
    timing_points: list[dict[str, Any]] = []
    hit_objects: list[dict[str, Any]] = []
    format_version = None

    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("osu file format v"):
            format_version = line.rsplit("v", 1)[-1]
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            sections.setdefault(section, [])
            continue
        sections.setdefault(section, []).append(line)

        if section == "General":
            item = _parse_key_value(line)
            if item:
                general[item[0]] = item[1]
        elif section == "Metadata":
            item = _parse_key_value(line)
            if item:
                metadata[item[0]] = item[1]
        elif section == "Difficulty":
            item = _parse_key_value(line)
            if item:
                difficulty[item[0]] = item[1]
        elif section == "TimingPoints":
            point = _parse_timing_point(line)
            if point:
                timing_points.append(point)
        elif section == "HitObjects":
            obj = _parse_hit_object(line, len(hit_objects))
            if obj:
                hit_objects.append(obj)

    stats = {
        "circles": sum(1 for obj in hit_objects if obj["kind"] == "circle"),
        "sliders": sum(1 for obj in hit_objects if obj["kind"] == "slider"),
        "spinners": sum(1 for obj in hit_objects if obj["kind"] == "spinner"),
        "holds": sum(1 for obj in hit_objects if obj["kind"] == "hold"),
        "total": len(hit_objects),
        "first_time": hit_objects[0]["time"] if hit_objects else None,
        "last_time": hit_objects[-1]["time"] if hit_objects else None,
    }

    return {
        "format_version": format_version,
        "source": source,
        "general": general,
        "metadata": metadata,
        "difficulty": difficulty,
        "timing_points": timing_points,
        "hit_objects": hit_objects,
        "stats": stats,
    }
