from __future__ import annotations

import math
from typing import Any

from nana.game.osu.calibration import PlayfieldRect, apply_screen_coordinates


DEFAULT_AIM_LEAD_MS = 80


def preview_next_objects(
    beatmap: dict[str, Any],
    *,
    current_time_ms: int | None = None,
    limit: int = 10,
    playfield_rect: PlayfieldRect | None = None,
) -> dict[str, Any]:
    objects = list(beatmap.get("hit_objects") or [])
    upcoming = []
    for obj in objects:
        if current_time_ms is not None and obj.get("time") is not None and int(obj["time"]) < int(current_time_ms):
            continue
        upcoming.append(obj)
        if len(upcoming) >= limit:
            break

    next_kind_counts = {}
    for obj in upcoming:
        kind = obj.get("kind") or "unknown"
        next_kind_counts[kind] = next_kind_counts.get(kind, 0) + 1

    next_objects = apply_screen_coordinates(upcoming, playfield_rect)
    return {
        "current_time_ms": current_time_ms,
        "limit": limit,
        "count": len(next_objects),
        "next_objects": next_objects,
        "kind_counts": next_kind_counts,
        "first_time": next_objects[0]["time"] if next_objects else None,
        "last_time": next_objects[-1]["time"] if next_objects else None,
        "calibrated": playfield_rect is not None,
    }


def preview_summary(
    beatmap: dict[str, Any],
    *,
    current_time_ms: int | None = None,
    limit: int = 10,
    playfield_rect: PlayfieldRect | None = None,
) -> str:
    preview = preview_next_objects(
        beatmap,
        current_time_ms=current_time_ms,
        limit=limit,
        playfield_rect=playfield_rect,
    )
    metadata = beatmap.get("metadata", {})
    title = metadata.get("Title") or metadata.get("title") or beatmap.get("title") or "unknown"
    version = metadata.get("Version") or metadata.get("difficulty") or beatmap.get("version") or "unknown"
    lines = [
        f"{title} [{version}]",
        f"  Current time: {preview['current_time_ms']}",
        f"  Next objects: {preview['count']}",
        f"  Calibrated: {preview['calibrated']}",
    ]
    for obj in preview["next_objects"]:
        screen = obj.get("screen") or {}
        screen_text = (
            f" screen=({screen.get('screen_x')},{screen.get('screen_y')})"
            if screen
            else " screen=uncalibrated"
        )
        lines.append(f"  #{obj.get('index')} {obj.get('kind')} t={obj.get('time')} x={obj.get('x')} y={obj.get('y')}{screen_text}")
    return "\n".join(lines)


def aim_plan_preview(
    beatmap: dict[str, Any],
    *,
    current_time_ms: int | None = None,
    lead_ms: int = DEFAULT_AIM_LEAD_MS,
    limit: int = 10,
    playfield_rect: PlayfieldRect | None = None,
) -> dict[str, Any]:
    preview = preview_next_objects(
        beatmap,
        current_time_ms=current_time_ms,
        limit=limit,
        playfield_rect=playfield_rect,
    )
    plan_objects = []
    current_time_value = int(current_time_ms) if current_time_ms is not None else None
    for obj in preview["next_objects"]:
        hit_time = obj.get("time")
        hit_time_value = int(hit_time) if hit_time is not None else None
        aim_time = hit_time_value - int(lead_ms) if hit_time_value is not None else None
        dt = hit_time_value - current_time_value if hit_time_value is not None and current_time_value is not None else None
        item = dict(obj)
        item["hit_time"] = hit_time_value
        item["aim_time"] = aim_time
        item["dt"] = dt
        plan_objects.append(item)
    return {
        "decision": "preview_only_no_input",
        "current_time_ms": current_time_ms,
        "lead_ms": int(lead_ms),
        "limit": int(limit),
        "count": len(plan_objects),
        "calibrated": preview.get("calibrated", False),
        "objects": plan_objects,
        "real_input": False,
        "submit": False,
    }


def aim_timeline_telemetry(
    beatmap: dict[str, Any],
    *,
    current_time_ms: int | None = None,
    lead_ms: int = DEFAULT_AIM_LEAD_MS,
    limit: int = 15,
    playfield_rect: PlayfieldRect | None = None,
) -> dict[str, Any]:
    plan = aim_plan_preview(
        beatmap,
        current_time_ms=current_time_ms,
        lead_ms=lead_ms,
        limit=limit,
        playfield_rect=playfield_rect,
    )
    telemetry_objects = []
    prev_screen = None
    prev_aim_time = None
    immediate_count = 0
    upcoming_count = 0
    for obj in plan["objects"]:
        dt = obj.get("dt")
        if dt is not None and 0 <= int(dt) <= 500:
            immediate_count += 1
        elif dt is not None and 500 < int(dt) <= 1500:
            upcoming_count += 1

        screen = obj.get("screen") or None
        screen_x = screen.get("screen_x") if screen else None
        screen_y = screen.get("screen_y") if screen else None
        aim_time = obj.get("aim_time")
        from_prev_px = None
        aim_speed_px_s = None
        if (
            screen_x is not None
            and screen_y is not None
            and prev_screen is not None
            and aim_time is not None
            and prev_aim_time is not None
        ):
            dx = float(screen_x) - float(prev_screen[0])
            dy = float(screen_y) - float(prev_screen[1])
            from_prev_px = round(math.hypot(dx, dy), 2)
            delta_ms = int(aim_time) - int(prev_aim_time)
            if delta_ms > 0:
                aim_speed_px_s = round(from_prev_px / (delta_ms / 1000.0), 2)

        item = dict(obj)
        item["from_prev_px"] = from_prev_px
        item["aim_speed_px_s"] = aim_speed_px_s
        telemetry_objects.append(item)

        if screen_x is not None and screen_y is not None:
            prev_screen = (float(screen_x), float(screen_y))
            prev_aim_time = aim_time
        else:
            prev_screen = None
            prev_aim_time = None

    return {
        "decision": "telemetry_only_no_input",
        "current_time_ms": plan.get("current_time_ms"),
        "lead_ms": plan.get("lead_ms"),
        "limit": plan.get("limit"),
        "count": len(telemetry_objects),
        "calibrated": plan.get("calibrated", False),
        "immediate_window_count": immediate_count,
        "upcoming_window_count": upcoming_count,
        "objects": telemetry_objects,
        "real_input": False,
        "submit": False,
    }


def aim_timeline_json_payload(
    beatmap: dict[str, Any],
    *,
    current_time_ms: int | None = None,
    lead_ms: int = DEFAULT_AIM_LEAD_MS,
    limit: int = 15,
    playfield_rect: PlayfieldRect | None = None,
) -> dict[str, Any]:
    timeline = aim_timeline_telemetry(
        beatmap,
        current_time_ms=current_time_ms,
        lead_ms=lead_ms,
        limit=limit,
        playfield_rect=playfield_rect,
    )
    objects = []
    for obj in timeline["objects"]:
        screen = obj.get("screen")
        objects.append(
            {
                "index": obj.get("index"),
                "kind": obj.get("kind"),
                "hit_t": obj.get("hit_time"),
                "aim_t": obj.get("aim_time"),
                "dt": obj.get("dt"),
                "playfield": {
                    "x": obj.get("x"),
                    "y": obj.get("y"),
                },
                "screen": (
                    {
                        "x": screen.get("screen_x"),
                        "y": screen.get("screen_y"),
                    }
                    if screen
                    else None
                ),
                "from_prev_px": obj.get("from_prev_px"),
                "aim_speed_px_s": obj.get("aim_speed_px_s"),
            }
        )
    return {
        "decision": "telemetry_json_only_no_input",
        "current_time_ms": timeline.get("current_time_ms"),
        "calibrated": timeline.get("calibrated", False),
        "lead_ms": timeline.get("lead_ms"),
        "count": timeline.get("count"),
        "windows": {
            "immediate_0_500_ms": timeline.get("immediate_window_count"),
            "upcoming_500_1500_ms": timeline.get("upcoming_window_count"),
        },
        "objects": objects,
        "real_input": False,
        "submit": False,
    }
