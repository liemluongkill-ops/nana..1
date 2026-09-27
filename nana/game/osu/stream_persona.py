from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from nana.game.osu.aim_status import read_aim_status
from nana.game.osu.reaction import read_vts_reaction_preview
from nana.runtime.stream_speak_gate import evaluate_stream_speak_gate

_HINT_BY_STATUS = {
    "tracking": "tracking",
    "fast_jump": "fast jump",
    "dense_pattern": "dense pattern",
    "idle": "idle break",
    "uncalibrated": "waiting for calibration",
    "stale": "waiting for fresh data",
    "unavailable": "waiting for data",
}

_LINE_BY_STATUS = {
    "tracking": "Đoạn này tôi đang bám nhịp ổn, giữ tập trung thôi.",
    "fast_jump": "Khúc này hơi căng, chuẩn bị nhảy nhanh nè.",
    "dense_pattern": "Đoạn này dày quá, phải khóa tập trung cao độ.",
    "idle": "Nghỉ một nhịp đã, lấy lại flow chút.",
    "uncalibrated": "Tôi đang chờ calibration ổn lại rồi mới đọc tiếp nhịp map.",
    "stale": "Tôi đang chờ dữ liệu mới hơn để bắt đúng nhịp.",
    "unavailable": "Tôi chưa có đủ dữ liệu map để nói gì chắc chắn cả.",
}

_API_ASSIGNMENT_PATTERN = re.compile(r"(?i)\b(?:api[_-]?key|token|secret|bearer|authorization)\b\s*[:=]\s*[^\s]+")
_WINDOWS_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:\\[^\r\n]+")
_UNIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])/(?:[^\s/]+/)+[^\s/]+")
_SECRET_PREFIX_PATTERN = re.compile(r"(?i)\b(?:sk|rk|pk)\-[A-Za-z0-9_-]{8,}\b")
_LONG_TOKEN_PATTERN = re.compile(r"\b[A-Za-z0-9_-]{24,}\b")
DEFAULT_OSU_STATE_PATH = Path(__file__).resolve().parent / "data" / "state.json"


def _looks_private(text: str) -> bool:
    return any(
        pattern.search(text)
        for pattern in (
            _API_ASSIGNMENT_PATTERN,
            _WINDOWS_PATH_PATTERN,
            _UNIX_PATH_PATTERN,
            _SECRET_PREFIX_PATTERN,
            _LONG_TOKEN_PATTERN,
        )
    )


def _sanitize_public_text(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    if not text:
        return fallback
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    if _looks_private(text):
        return fallback
    return text


def _read_state_metadata(source_path: Path) -> dict[str, Any]:
    try:
        raw = source_path.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    beatmap = payload.get("beatmap")
    return beatmap if isinstance(beatmap, dict) else {}


def _read_sidecar_metadata(source_path: Path) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for candidate in [source_path.with_suffix(".osu")]:
        try:
            raw = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in raw.splitlines():
            stripped = line.strip()
            if stripped.startswith("Title:") and "Title" not in metadata:
                metadata["Title"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Version:") and "Version" not in metadata:
                metadata["Version"] = stripped.split(":", 1)[1].strip()
            elif stripped.startswith("Artist:") and "Artist" not in metadata:
                metadata["Artist"] = stripped.split(":", 1)[1].strip()
            if {"Title", "Version"}.issubset(metadata):
                break
        if metadata:
            return metadata
    return metadata


def _resolve_song_metadata(metadata_path: Path | None) -> tuple[str, str, str]:
    metadata: dict[str, Any] = {}
    if metadata_path is not None:
        metadata = _read_state_metadata(metadata_path)
        if not metadata:
            metadata = _read_sidecar_metadata(metadata_path)

    title = _sanitize_public_text(metadata.get("TitleUnicode") or metadata.get("Title"), fallback="unknown")
    difficulty = _sanitize_public_text(metadata.get("Version"), fallback="unknown")
    artist = _sanitize_public_text(metadata.get("ArtistUnicode") or metadata.get("Artist"), fallback="unknown")
    return title, difficulty, artist


def build_stream_persona_preview(
    *,
    source_path: Path,
    stale_ms: int,
    metadata_path: Path | None = None,
) -> dict[str, Any]:
    status_payload = read_aim_status(source_path, stale_ms=stale_ms)
    reaction_payload = read_vts_reaction_preview(source_path=source_path, stale_ms=stale_ms)
    osu_status = str(status_payload.get("status") or "unavailable")
    reaction = str(reaction_payload.get("reaction") or "offline")
    title, difficulty, artist = _resolve_song_metadata(metadata_path or DEFAULT_OSU_STATE_PATH)
    safe_hint = _HINT_BY_STATUS.get(osu_status, "waiting for data")
    line = _LINE_BY_STATUS.get(osu_status, _LINE_BY_STATUS["unavailable"])

    public_event = {
        "osu_status": osu_status,
        "reaction": reaction,
        "song_title": title,
        "difficulty": difficulty,
        "artist": artist,
        "safe_hint": safe_hint,
    }

    gate = evaluate_stream_speak_gate(public_event)

    if not gate["speak_allowed"]:
        line = ""

    return {
        "decision": "stream_persona_preview_only_no_output",
        "public_event": public_event,
        "line": line,
        "stream_mode": gate["stream_mode"],
        "speak_allowed": gate["speak_allowed"],
        "voice_allowed": gate["voice_allowed"],
        "subtitle_allowed": gate["subtitle_allowed"],
        "gate_reason": gate["reason"],
        "private_data": "filtered",
        "voice_call": False,
        "vts_call": False,
        "real_input": False,
        "submit": False,
    }
