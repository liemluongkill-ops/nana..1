#!/usr/bin/env python3
"""Export Nana's firmware face catalog as owner-review GIF files."""

from __future__ import annotations

import argparse
import html
import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageDraw


WIDTH = 280
HEIGHT = 240
LEFT_EYE_X = 83
RIGHT_EYE_X = 197
EYE_Y = 147
FRAME_MS = 50
TRANSITION_MS = 350

BLACK = 0
EYE = 1
BRIGHT = 2
MID = 3
DIM = 4

PALETTE = [
    0, 0, 0,
    35, 205, 255,
    72, 238, 255,
    24, 143, 190,
    5, 45, 72,
] + [0, 0, 0] * (256 - 5)


@dataclass(frozen=True)
class Stage:
    index: int
    tag: str
    duration_ms: int
    kind: str

    @property
    def filename(self) -> str:
        return f"{self.index:02d}_{self.kind}_{self.tag}.gif"


@dataclass
class Scene:
    tag: str
    local_ms: int
    duration_ms: int
    kind: str
    eye_width: list[int] = field(default_factory=lambda: [82, 82])
    eye_height: list[int] = field(default_factory=lambda: [72, 72])
    eye_offset_x: list[int] = field(default_factory=lambda: [0, 0])
    eye_offset_y: list[int] = field(default_factory=lambda: [0, 0])
    eye_top_tilt: list[int] = field(default_factory=lambda: [0, 0])
    eye_bottom_tilt: list[int] = field(default_factory=lambda: [0, 0])
    eye_style: str = "rounded"
    arc_depth: int = 10
    arc_thickness: int = 8
    mark: str = "none"
    blink: list[int] = field(default_factory=lambda: [0, 0])
    show_spinner: bool = False
    show_question: bool = False
    show_panic_mouth: bool = False


def parse_stages(source_path: Path) -> list[Stage]:
    source = source_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(EXPRESSION|GESTURE)_STAGE\(NANA_FACE_[A-Z_]+,\s*"
        r'"([^"]+)",\s*(\d+)U\)'
    )
    stages = [
        Stage(index, match.group(2), int(match.group(3)), match.group(1).lower())
        for index, match in enumerate(pattern.finditer(source), start=1)
    ]
    if not stages:
        raise RuntimeError("Firmware tag table is empty")
    if len({stage.tag for stage in stages}) != len(stages):
        raise RuntimeError("Firmware tag table contains duplicates")
    return stages


def transition_blink(local_ms: int, duration_ms: int) -> int:
    if local_ms < TRANSITION_MS:
        return 1000 - local_ms * 1000 // TRANSITION_MS
    remaining_ms = duration_ms - local_ms
    if remaining_ms < TRANSITION_MS:
        return 1000 - remaining_ms * 1000 // TRANSITION_MS
    return 0


def natural_blink(local_ms: int) -> int:
    blink_time = local_ms % 3600
    if blink_time < 2450 or blink_time >= 2690:
        return 0
    if blink_time < 2570:
        return (blink_time - 2450) * 1000 // 120
    return (2690 - blink_time) * 1000 // 120


def smooth_step(elapsed_ms: int, duration_ms: int) -> int:
    if duration_ms == 0 or elapsed_ms >= duration_ms:
        return 1000
    progress = elapsed_ms / duration_ms
    return int(progress * progress * (3.0 - 2.0 * progress) * 1000.0)


def gesture_envelope(
    local_ms: int,
    start_ms: int,
    rise_ms: int,
    hold_ms: int,
    fall_ms: int,
) -> int:
    if local_ms < start_ms:
        return 0
    elapsed_ms = local_ms - start_ms
    if elapsed_ms < rise_ms:
        return smooth_step(elapsed_ms, rise_ms)
    elapsed_ms -= rise_ms
    if elapsed_ms < hold_ms:
        return 1000
    elapsed_ms -= hold_ms
    if elapsed_ms < fall_ms:
        return 1000 - smooth_step(elapsed_ms, fall_ms)
    return 0


def apply_blink_both(scene: Scene, value: int) -> None:
    scene.blink[0] = max(scene.blink[0], value)
    scene.blink[1] = max(scene.blink[1], value)


def offset_both(scene: Scene, x: int, y: int) -> None:
    scene.eye_offset_x[0] += x
    scene.eye_offset_x[1] += x
    scene.eye_offset_y[0] += y
    scene.eye_offset_y[1] += y


def build_scene(stage: Stage, local_ms: int) -> Scene:
    scene = Scene(stage.tag, local_ms, stage.duration_ms, stage.kind)
    blink = transition_blink(local_ms, stage.duration_ms)
    scene.blink = [blink, blink]
    seconds = local_ms / 1000.0
    tag = stage.tag

    if tag == "neutral":
        scene.eye_height[0] += int(math.sin(seconds * 1.4) * 2.0)
        scene.eye_height[1] = scene.eye_height[0]
    elif tag == "listening":
        scene.eye_width = [70, 70]
        scene.eye_height[0] = 58 + int(math.sin(seconds * 4.2) * 2.0)
        scene.eye_height[1] = scene.eye_height[0]
    elif tag == "pondering":
        scene.eye_width = [76, 76]
        scene.eye_height = [42, 42]
        scene.eye_offset_y = [-3, -3]
    elif tag == "happy":
        scene.eye_style = "arc"
        scene.eye_width = [72, 72]
    elif tag == "laughing":
        scene.eye_style = "arc"
        scene.eye_width = [76, 76]
        scene.arc_depth = 15
        scene.arc_thickness = 12
        scene.eye_offset_y[0] = int(math.sin(seconds * 9.0) * 2.0)
        scene.eye_offset_y[1] = scene.eye_offset_y[0]
    elif tag == "glee":
        scene.eye_style = "arc"
        scene.eye_width = [84, 84]
        scene.arc_depth = 20
        scene.arc_thickness = 10
    elif tag == "angry":
        scene.eye_style = "slanted"
        scene.eye_width = [84, 84]
        scene.eye_height = [46, 46]
        scene.eye_top_tilt = [20, -20]
        scene.eye_bottom_tilt = [4, -4]
    elif tag == "furious":
        scene.eye_style = "slanted"
        scene.eye_width = [86, 86]
        scene.eye_height = [54, 54]
        scene.eye_top_tilt = [16, -16]
        scene.eye_bottom_tilt = [2, -2]
    elif tag == "frustrated":
        shake = int(math.sin(seconds * 19.0) * 4.0)
        scene.eye_style = "slanted"
        scene.eye_width = [84, 84]
        scene.eye_height = [46, 46]
        scene.eye_offset_x = [shake, shake]
        scene.eye_top_tilt = [20, -20]
        scene.eye_bottom_tilt = [4, -4]
    elif tag in {"sad", "crying"}:
        scene.eye_style = "slanted"
        scene.eye_width = [78, 78]
        scene.eye_height = [38, 38]
        scene.eye_offset_y = [7, 7]
        scene.eye_top_tilt = [-14, 14]
        scene.eye_bottom_tilt = [-5, 5]
        if tag == "crying":
            scene.mark = "tear"
    elif tag == "sleepy":
        scene.eye_style = "line"
        scene.eye_width = [78, 78]
        scene.eye_offset_y = [9, 9]
    elif tag == "tired":
        scene.eye_style = "slanted"
        scene.eye_width = [78, 78]
        scene.eye_height = [20, 20]
        scene.eye_offset_y = [10, 10]
        scene.eye_top_tilt = [-8, 8]
        scene.eye_bottom_tilt = [-4, 4]
    elif tag == "bored":
        scene.eye_width = [80, 80]
        scene.eye_height = [16, 16]
        scene.eye_offset_y = [8, 8]
    elif tag == "worried":
        scene.eye_style = "slanted"
        scene.eye_width = [80, 80]
        scene.eye_height = [58, 58]
        scene.eye_top_tilt = [-16, 16]
        scene.eye_bottom_tilt = [-3, 3]
    elif tag in {"nervous", "anxious"}:
        scene.eye_style = "slanted"
        width = 80 if tag == "nervous" else 78
        height = 58 if tag == "nervous" else 42
        tilt = 14 if tag == "nervous" else 10
        scene.eye_width = [width, width]
        scene.eye_height = [height, height]
        scene.eye_top_tilt = [tilt, -tilt]
        scene.mark = "sweat"
    elif tag == "surprised":
        scene.eye_width = [90, 90]
        scene.eye_height = [90, 90]
    elif tag == "shocked":
        height = 102 + int(math.sin(seconds * 5.0) * 2.0)
        scene.eye_width = [94, 94]
        scene.eye_height = [height, height]
    elif tag == "scared":
        scene.eye_width = [74, 68]
        scene.eye_height = [86, 76]
        scene.eye_offset_x = [-5, 4]
        scene.eye_offset_y[1] = 4
    elif tag == "awe":
        scene.eye_width = [94, 94]
        scene.eye_height = [106, 106]
    elif tag == "skeptical":
        scene.eye_width = [66, 52]
        scene.eye_height = [64, 30]
        scene.eye_offset_x = [-7, -4]
        scene.eye_offset_y[1] = 5
    elif tag == "suspicious":
        scene.eye_style = "slanted"
        scene.eye_width = [78, 68]
        scene.eye_height = [42, 30]
        scene.eye_offset_y[1] = 6
        scene.eye_top_tilt = [-10, 9]
        scene.eye_bottom_tilt = [-4, 4]
    elif tag == "focused":
        scene.eye_width = [80, 80]
        scene.eye_height = [22, 22]
        scene.eye_offset_y = [4, 4]
    elif tag == "squint":
        scene.eye_width = [68, 68]
        scene.eye_height = [12, 12]
        scene.eye_offset_y = [5, 5]
    elif tag == "annoyed":
        scene.eye_style = "slanted"
        scene.eye_width = [78, 72]
        scene.eye_height = [42, 34]
        scene.eye_offset_y[1] = 5
        scene.eye_top_tilt = [-10, 12]
        scene.eye_bottom_tilt = [-4, 5]
    elif tag == "unimpressed":
        scene.eye_style = "slanted"
        scene.eye_width = [76, 68]
        scene.eye_height = [30, 22]
        scene.eye_offset_y[1] = 5
        scene.eye_top_tilt = [-8, 8]
        scene.eye_bottom_tilt = [-3, 4]
    elif tag == "mic_unclear":
        scene.eye_width = [80, 80]
        scene.eye_height = [30, 30]
        scene.eye_offset_y = [3, 3]
        scene.show_question = True
    elif tag == "core_loading":
        scene.eye_width = [80, 80]
        scene.eye_height = [58, 58]
        scene.eye_offset_y = [5, 5]
        scene.show_spinner = True
    elif tag == "curious":
        look_x = int(math.sin(seconds * 1.7) * 15.0)
        scene.eye_width = [72, 72]
        scene.eye_height = [88 if look_x < -3 else 64,
                            88 if look_x > 3 else 64]
        offset_both(scene, look_x, -2)
    elif tag == "confused":
        shake = int(math.sin(seconds * 15.0) * 4.0)
        scene.eye_style = "slanted"
        scene.eye_width = [82, 62]
        scene.eye_height = [58, 38]
        scene.eye_offset_x = [shake - 4, shake + 5]
        scene.eye_offset_y = [-3, 6]
        scene.eye_top_tilt = [-5, 9]
    elif tag == "playful":
        wink_time = local_ms % 3200
        scene.eye_width = [88, 70]
        scene.eye_height = [70, 60]
        scene.eye_offset_y = [int(math.sin(seconds * 3.0) * 2.0) - 2, 4]
        scene.blink[1] = max(
            scene.blink[1],
            gesture_envelope(wink_time, 1450, 100, 130, 180),
        )
    elif tag == "smug":
        scene.eye_style = "slanted"
        scene.eye_width = [80, 68]
        scene.eye_height = [28, 38]
        scene.eye_offset_y = [3, -2]
        scene.eye_top_tilt = [-7, 5]
        scene.eye_bottom_tilt = [-3, 2]
    elif tag == "shy":
        scene.eye_style = "arc"
        scene.eye_width = [62, 62]
        scene.arc_depth = 12
        scene.arc_thickness = 8
        scene.eye_offset_x = [5, -5]
        scene.eye_offset_y = [8, 8]
    elif tag == "relieved":
        scene.eye_style = "arc"
        scene.eye_width = [80, 80]
        scene.arc_depth = 7
        scene.arc_thickness = 7
        scene.eye_offset_y = [5, 5]
    elif tag == "determined":
        scene.eye_style = "slanted"
        scene.eye_width = [88, 88]
        scene.eye_height = [34, 34]
        scene.eye_top_tilt = [11, -11]
        scene.eye_bottom_tilt = [2, -2]
        scene.eye_offset_y = [4, 4]
    elif tag == "pleading":
        height = 96 + int(math.sin(seconds * 2.2) * 3.0)
        scene.eye_width = [64, 64]
        scene.eye_height = [height, height]
        scene.eye_offset_x = [5, -5]
        scene.eye_offset_y = [5, 5]
    elif tag == "love":
        pulse = int((math.sin(seconds * 4.2) + 1.0) * 3.0)
        scene.eye_style = "heart"
        scene.eye_width = [68 + pulse, 68 + pulse]
        scene.eye_height = [72 + pulse, 72 + pulse]
    elif tag == "dizzy":
        scene.eye_style = "spiral"
        scene.eye_width = [72, 72]
        scene.eye_height = [72, 72]
    elif tag == "proud":
        scene.eye_style = "arc"
        scene.eye_width = [82, 72]
        scene.arc_depth = 13
        scene.arc_thickness = 9
        scene.eye_offset_y = [-4, 1]
    elif tag == "wink_left":
        scene.blink[0] = max(
            scene.blink[0], gesture_envelope(local_ms, 470, 100, 180, 210)
        )
        scene.eye_width[1] = 86
        scene.eye_height[1] = 76
    elif tag == "wink_right":
        scene.blink[1] = max(
            scene.blink[1], gesture_envelope(local_ms, 470, 100, 180, 210)
        )
        scene.eye_width[0] = 86
        scene.eye_height[0] = 76
    elif tag == "double_blink":
        apply_blink_both(
            scene,
            max(
                gesture_envelope(local_ms, 390, 90, 70, 140),
                gesture_envelope(local_ms, 870, 90, 70, 150),
            ),
        )
    elif tag in {"glance_left", "glance_right", "glance_up", "glance_down"}:
        envelope = gesture_envelope(local_ms, 360, 260, 500, 280)
        horizontal = -22 if tag == "glance_left" else (22 if tag == "glance_right" else 0)
        vertical = -18 if tag == "glance_up" else (18 if tag == "glance_down" else 0)
        offset_both(scene, horizontal * envelope // 1000, vertical * envelope // 1000)
    elif tag == "look_around":
        progress = local_ms / stage.duration_ms
        offset_both(
            scene,
            int(math.sin(progress * math.tau) * 20.0),
            int(math.sin(progress * math.tau * 2.0) * 9.0),
        )
    elif tag == "scan":
        progress = local_ms / stage.duration_ms
        offset_both(scene, int(math.sin(progress * math.tau) * 26.0), 0)
        scene.eye_height = [46, 46]
    elif tag in {"peek_left", "peek_right"}:
        envelope = gesture_envelope(local_ms, 350, 260, 650, 300)
        left = tag == "peek_left"
        direction = -1 if left else 1
        offset_both(scene, direction * 25 * envelope // 1000, 0)
        scene.eye_height[0 if left else 1] += 22 * envelope // 1000
        scene.eye_height[1 if left else 0] -= 10 * envelope // 1000
    elif tag == "startled_blink":
        surprise = gesture_envelope(local_ms, 300, 160, 350, 220)
        scene.eye_width[0] += 12 * surprise // 1000
        scene.eye_width[1] += 12 * surprise // 1000
        scene.eye_height[0] += 24 * surprise // 1000
        scene.eye_height[1] += 24 * surprise // 1000
        apply_blink_both(scene, gesture_envelope(local_ms, 1020, 80, 60, 140))
    elif tag == "double_take":
        right = gesture_envelope(local_ms, 260, 150, 240, 140)
        left = gesture_envelope(local_ms, 820, 140, 300, 180)
        offset_both(scene, 22 * (right - left) // 1000, 0)
        apply_blink_both(scene, gesture_envelope(local_ms, 700, 70, 40, 110))
    elif tag == "celebrate":
        scene.eye_style = "arc"
        scene.eye_width = [80, 80]
        scene.arc_depth = 15
        scene.arc_thickness = 10
        bounce = -int(abs(math.sin(seconds * 8.0)) * 10.0)
        scene.eye_offset_y = [bounce, bounce]
        scene.mark = "sparkles"
    elif tag == "nope":
        shake = int(math.sin(seconds * 17.0) * 7.0)
        scene.eye_style = "slanted"
        scene.eye_width = [74, 74]
        scene.eye_height = [28, 28]
        scene.eye_top_tilt = [-8, 8]
        scene.eye_offset_x = [shake, shake]
    elif tag == "panic":
        shake = int(math.sin(seconds * 24.0) * 4.0)
        scene.eye_style = "chevron"
        scene.eye_width = [74, 74]
        scene.eye_height = [46, 46]
        scene.eye_offset_x = [shake, shake]
        scene.show_panic_mouth = 300 <= local_ms and local_ms + 300 < stage.duration_ms
    else:
        raise RuntimeError(f"No preview scene implementation for tag: {tag}")

    if stage.kind == "expression" and tag != "sleepy":
        apply_blink_both(scene, natural_blink(local_ms))
    if tag not in {"sleepy", "frustrated"}:
        breathe = int(math.sin(seconds * 1.8) * 1.4)
        scene.eye_offset_y[0] += breathe
        scene.eye_offset_y[1] += breathe
    return scene


def circle(draw: ImageDraw.ImageDraw, x: int, y: int, radius: int, color: int) -> None:
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color)


def capsule(
    draw: ImageDraw.ImageDraw,
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    radius: int,
    color: int,
) -> None:
    draw.line((start_x, start_y, end_x, end_y), fill=color, width=radius * 2 + 1)
    circle(draw, start_x, start_y, radius, color)
    circle(draw, end_x, end_y, radius, color)


def rounded_eye(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    color: int,
) -> None:
    left = center_x - width // 2
    top = center_y - height // 2
    draw.rounded_rectangle(
        (left, top, left + width - 1, top + height - 1),
        radius=min(22, height // 2),
        fill=color,
    )


def slanted_eye(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    top_tilt: int,
    bottom_tilt: int,
    color: int,
) -> None:
    left = center_x - width // 2
    for column in range(width):
        top = center_y - height // 2 - top_tilt // 2 + top_tilt * column // max(width - 1, 1)
        bottom = center_y + height // 2 - bottom_tilt // 2 + bottom_tilt * column // max(width - 1, 1)
        edge_distance = min(column, width - 1 - column)
        edge_inset = max(3 - edge_distance, 0)
        draw.line(
            (left + column, top + edge_inset, left + column, bottom - edge_inset),
            fill=color,
        )


def arc_eye(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    center_y: int,
    width: int,
    depth: int,
    thickness: int,
    color: int,
) -> None:
    half_width = max(width // 2, 1)
    denominator = half_width * half_width
    radius = max(thickness // 2, 2)
    for dx in range(-half_width, half_width + 1):
        curve_y = center_y + dx * dx * depth // denominator
        circle(draw, center_x + dx, curve_y, radius, color)


def heart_eye(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    color: int,
) -> None:
    width = max(width, 12)
    height = max(height, 12)
    radius = max(min(width // 4, height // 4), 3)
    lobe_y = center_y - height // 4
    circle(draw, center_x - radius, lobe_y, radius, color)
    circle(draw, center_x + radius, lobe_y, radius, color)
    body_bottom = center_y + height // 2
    body_height = max(body_bottom - lobe_y, 1)
    for y in range(lobe_y, body_bottom + 1):
        half_width = (width // 2) * (body_bottom - y) // body_height
        draw.line((center_x - half_width, y, center_x + half_width, y), fill=color)


def spiral_eye(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    phase: float,
    direction: int,
    color: int,
) -> None:
    maximum_radius = max(min(width, height) // 2 - 2, 5)
    previous_x = center_x
    previous_y = center_y
    for segment in range(1, 41):
        progress = segment / 40.0
        radius = 2.0 + (maximum_radius - 2) * progress
        angle = phase + direction * progress * 14.2
        x = center_x + int(math.cos(angle) * radius)
        y = center_y + int(math.sin(angle) * radius)
        capsule(draw, previous_x, previous_y, x, y, 2, color)
        previous_x, previous_y = x, y


def chevron_eye(
    draw: ImageDraw.ImageDraw,
    center_x: int,
    center_y: int,
    width: int,
    height: int,
    points_right: bool,
    color: int,
) -> None:
    outer_x = center_x + (-width // 2 if points_right else width // 2)
    inner_x = center_x + (width // 3 if points_right else -width // 3)
    radius = max(height // 11, 3)
    capsule(draw, outer_x, center_y - height // 2, inner_x, center_y, radius, color)
    capsule(draw, inner_x, center_y, outer_x, center_y + height // 2, radius, color)


def draw_spinner(draw: ImageDraw.ImageDraw, scene: Scene) -> None:
    if not scene.show_spinner:
        return
    offsets = [(0, -18), (13, -13), (18, 0), (13, 13),
               (0, 18), (-13, 13), (-18, 0), (-13, -13)]
    phase = (scene.local_ms // 110) % len(offsets)
    for index, (offset_x, offset_y) in enumerate(offsets):
        age = (phase + len(offsets) - index) % len(offsets)
        color = BRIGHT if age == 0 else (MID if age <= 2 else DIM)
        circle(draw, 140 + offset_x, 49 + offset_y, 4, color)


def draw_question(draw: ImageDraw.ImageDraw, scene: Scene) -> None:
    if not scene.show_question:
        return
    bob = int(math.sin(scene.local_ms / 260.0) * 2.0)
    x, y = 248, 101 + bob
    points = [(-8, -9), (-4, -13), (2, -13), (6, -8), (6, -3), (0, 2), (0, 6)]
    for start, end in zip(points, points[1:]):
        capsule(draw, x + start[0], y + start[1], x + end[0], y + end[1], 2, BRIGHT)
    circle(draw, x, y + 13, 3, BRIGHT)


def draw_mark(draw: ImageDraw.ImageDraw, scene: Scene) -> None:
    bob = int(math.sin(scene.local_ms / 210.0) * 2.0)
    if scene.mark == "tear":
        capsule(draw, 247, 160 + bob, 247, 174 + bob, 3, BRIGHT)
        circle(draw, 247, 177 + bob, 5, BRIGHT)
    elif scene.mark == "sweat":
        capsule(draw, 246, 100 + bob, 242, 111 + bob, 3, BRIGHT)
        circle(draw, 241, 114 + bob, 4, BRIGHT)
    elif scene.mark == "sparkles":
        pulse = 7 + int((math.sin(scene.local_ms / 150.0) + 1.0) * 2.0)
        capsule(draw, 32 - pulse, 96, 32 + pulse, 96, 2, BRIGHT)
        capsule(draw, 32, 96 - pulse, 32, 96 + pulse, 2, BRIGHT)
        capsule(draw, 249 - pulse // 2, 109, 249 + pulse // 2, 109, 2, MID)
        capsule(draw, 249, 109 - pulse // 2, 249, 109 + pulse // 2, 2, MID)


def draw_eye(draw: ImageDraw.ImageDraw, scene: Scene, index: int) -> None:
    center_x = [LEFT_EYE_X, RIGHT_EYE_X][index] + scene.eye_offset_x[index]
    center_y = EYE_Y + scene.eye_offset_y[index]
    width = scene.eye_width[index]
    openness = 1000 - scene.blink[index]
    if scene.eye_style == "arc":
        depth = scene.arc_depth * openness // 1000
        thickness = 6 + max(scene.arc_thickness - 6, 0) * openness // 1000
        arc_eye(draw, center_x, center_y, width, depth, thickness, EYE)
        return
    if scene.eye_style == "line":
        capsule(draw, center_x - width // 2, center_y,
                center_x + width // 2, center_y, 4, EYE)
        return
    visible_height = max(scene.eye_height[index] * openness // 1000, 5)
    if visible_height <= 10:
        capsule(draw, center_x - width // 3, center_y,
                center_x + width // 3, center_y, 3, EYE)
        return
    if scene.eye_style == "heart":
        heart_eye(draw, center_x, center_y, width, visible_height, EYE)
    elif scene.eye_style == "spiral":
        spiral_eye(
            draw,
            center_x,
            center_y,
            width,
            visible_height,
            scene.local_ms / 230.0,
            1 if index == 0 else -1,
            EYE,
        )
    elif scene.eye_style == "chevron":
        chevron_eye(draw, center_x, center_y, width, visible_height, index == 0, EYE)
    elif scene.eye_style == "slanted":
        source_height = max(scene.eye_height[index], 1)
        slanted_eye(
            draw,
            center_x,
            center_y,
            width,
            visible_height,
            scene.eye_top_tilt[index] * visible_height // source_height,
            scene.eye_bottom_tilt[index] * visible_height // source_height,
            EYE,
        )
    else:
        rounded_eye(draw, center_x, center_y, width, visible_height, EYE)


def draw_panic_mouth(draw: ImageDraw.ImageDraw, scene: Scene) -> None:
    if not scene.show_panic_mouth:
        return
    seconds = scene.local_ms / 1000.0
    center_x = 140 + int(math.sin(seconds * 24.0) * 3.0)
    center_y = 202 + int(math.sin(seconds * 10.0) * 2.0)
    points = [(-31, -2), (-17, -7), (0, 2), (17, -7), (31, -2)]
    for start, end in zip(points, points[1:]):
        capsule(
            draw,
            center_x + start[0],
            center_y + start[1],
            center_x + end[0],
            center_y + end[1],
            4,
            EYE,
        )


def render_frame(stage: Stage, local_ms: int) -> Image.Image:
    image = Image.new("P", (WIDTH, HEIGHT), BLACK)
    image.putpalette(PALETTE)
    draw = ImageDraw.Draw(image)
    scene = build_scene(stage, local_ms)
    draw_spinner(draw, scene)
    draw_question(draw, scene)
    draw_mark(draw, scene)
    draw_eye(draw, scene, 0)
    draw_eye(draw, scene, 1)
    draw_panic_mouth(draw, scene)
    return image


def write_gallery(output_dir: Path, stages: list[Stage]) -> None:
    animation_dir = output_dir / "animations"
    output_dir.mkdir(parents=True, exist_ok=False)
    animation_dir.mkdir()

    manifest = []
    for stage in stages:
        frame_count = math.ceil(stage.duration_ms / FRAME_MS)
        frames = [
            render_frame(stage, min(frame * FRAME_MS, stage.duration_ms - 1))
            for frame in range(frame_count)
        ]
        path = animation_dir / stage.filename
        frames[0].save(
            path,
            save_all=True,
            append_images=frames[1:],
            duration=FRAME_MS,
            loop=0,
            disposal=2,
            optimize=False,
        )
        manifest.append(
            {
                "index": stage.index,
                "tag": stage.tag,
                "kind": stage.kind,
                "duration_ms": stage.duration_ms,
                "file": f"animations/{stage.filename}",
            }
        )
        print(f"[{stage.index:02d}/{len(stages)}] {stage.kind:10s} {stage.tag}")

    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="ascii"
    )
    (output_dir / "README.txt").write_text(
        "NANA ANIMATION OWNER REVIEW\n"
        "\n"
        "1. Open index.html to review all animations.\n"
        "2. Delete unwanted GIF files only from the animations folder.\n"
        "3. Keep index.html, manifest.json, and README.txt.\n"
        "4. Tell Codex when selection is complete.\n",
        encoding="ascii",
    )

    cards = "\n".join(
        f'''<article class="card {stage.kind}">
  <img src="animations/{html.escape(stage.filename)}" alt="{html.escape(stage.tag)}">
  <div><strong>{stage.index:02d}. {html.escape(stage.tag)}</strong><span>{stage.kind}</span></div>
</article>'''
        for stage in stages
    )
    page = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Nana Animation Review</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #101214; color: #eaf8ff; font: 15px Segoe UI, sans-serif; }}
  header {{ position: sticky; top: 0; z-index: 2; padding: 18px 24px; background: #101214ee; border-bottom: 1px solid #29343a; }}
  h1 {{ margin: 0 0 6px; font-size: 23px; letter-spacing: 0; }}
  p {{ margin: 0; color: #9fb2bc; }}
  main {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px; padding: 18px; }}
  .card {{ overflow: hidden; border: 1px solid #28353c; border-radius: 8px; background: #171b1e; }}
  .card.gesture {{ border-color: #176b83; }}
  img {{ display: block; width: 100%; aspect-ratio: 7 / 6; object-fit: contain; image-rendering: pixelated; background: #000; }}
  .card div {{ display: flex; justify-content: space-between; gap: 12px; align-items: center; padding: 10px 12px; }}
  strong {{ font-size: 15px; }}
  span {{ color: #67d9ff; font-size: 12px; text-transform: uppercase; }}
</style>
</head>
<body>
<header>
  <h1>Nana Animation Review - {len(stages)} tags</h1>
  <p>Delete unwanted GIF files from the animations folder, then tell Codex the selection is complete.</p>
</header>
<main>
{cards}
</main>
</body>
</html>
"""
    (output_dir / "index.html").write_text(page, encoding="ascii")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "output" / "face-gallery",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "main" / "nana_display.c",
    )
    args = parser.parse_args()
    stages = parse_stages(args.source)
    write_gallery(args.output, stages)
    print(f"Gallery ready: {args.output / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
