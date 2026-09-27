from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    import win32gui

    WIN32_CURSOR_AVAILABLE = True
except ImportError:
    win32gui = None
    WIN32_CURSOR_AVAILABLE = False


PLAYFIELD_WIDTH = 512
PLAYFIELD_HEIGHT = 384
OSU_PLAYFIELD_RECT_ENV = "NANA_OSU_PLAYFIELD_RECT"


@dataclass(frozen=True)
class PlayfieldRect:
    left: float
    top: float
    width: float
    height: float
    source: str

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height


def parse_playfield_rect(raw: str | None = None) -> PlayfieldRect | None:
    text = str(raw if raw is not None else os.getenv(OSU_PLAYFIELD_RECT_ENV) or "").strip()
    if not text:
        return None
    separators = "," if "," in text else " "
    parts = [part.strip() for part in text.split(separators) if part.strip()]
    if len(parts) != 4:
        raise ValueError("expected_playfield_rect_left_top_width_height")
    left, top, width, height = [float(part) for part in parts]
    if width <= 0 or height <= 0:
        raise ValueError("playfield_rect_width_height_must_be_positive")
    return PlayfieldRect(left=left, top=top, width=width, height=height, source=OSU_PLAYFIELD_RECT_ENV)


def playfield_to_screen(x: float, y: float, rect: PlayfieldRect) -> dict[str, Any]:
    screen_x = rect.left + (float(x) / PLAYFIELD_WIDTH) * rect.width
    screen_y = rect.top + (float(y) / PLAYFIELD_HEIGHT) * rect.height
    return {
        "x": float(x),
        "y": float(y),
        "screen_x": round(screen_x, 2),
        "screen_y": round(screen_y, 2),
        "rect": {
            "left": rect.left,
            "top": rect.top,
            "width": rect.width,
            "height": rect.height,
            "right": rect.right,
            "bottom": rect.bottom,
            "source": rect.source,
        },
    }


def apply_screen_coordinates(objects: list[dict[str, Any]], rect: PlayfieldRect | None) -> list[dict[str, Any]]:
    converted = []
    for obj in objects:
        item = dict(obj)
        if rect is not None and obj.get("x") is not None and obj.get("y") is not None:
            item["screen"] = playfield_to_screen(obj["x"], obj["y"], rect)
        else:
            item["screen"] = None
        converted.append(item)
    return converted


def read_mouse_position() -> dict[str, Any]:
    if not WIN32_CURSOR_AVAILABLE or win32gui is None:
        return {
            "ok": False,
            "x": None,
            "y": None,
            "error": "cursor_api_unavailable",
            "real_input": False,
        }
    try:
        x, y = win32gui.GetCursorPos()
    except Exception as exc:
        return {
            "ok": False,
            "x": None,
            "y": None,
            "error": f"cursor_pos_failed:{exc}",
            "real_input": False,
        }
    return {
        "ok": True,
        "x": int(x),
        "y": int(y),
        "error": None,
        "real_input": False,
    }


def calibration_rect_from_corners(*, left: float, top: float, right: float, bottom: float) -> PlayfieldRect:
    width = float(right) - float(left)
    height = float(bottom) - float(top)
    if width <= 0 or height <= 0:
        raise ValueError("playfield_rect_right_bottom_must_exceed_left_top")
    return PlayfieldRect(
        left=float(left),
        top=float(top),
        width=width,
        height=height,
        source="manual_corners",
    )
