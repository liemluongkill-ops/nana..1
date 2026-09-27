from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen


TOSU_BASE_URL_ENV = "NANA_OSU_TOSU_BASE_URL"
TOSU_BASE_URL_DEFAULT = "http://127.0.0.1:24050"
OSU_CURRENT_MAP_PATH_ENV = "NANA_OSU_CURRENT_MAP_PATH"
OSU_STATE_PATH_ENV = "NANA_OSU_STATE_PATH"
OSU_DATA_DIR = Path(__file__).resolve().parent / "data"
OSU_STATE_PATH_DEFAULT = OSU_DATA_DIR / "state.json"
CURRENT_BEATMAP_ENDPOINTS = (
    "/json/v2",
    "/websocket/v2",
    "/websocket/v2/precise",
    "/json",
    "/ws",
)
CURRENT_BEATMAP_FILE_ENDPOINTS = (
    "/files/beatmap/file",
    "/files/beatmap/{path}",
    "/Songs/{path}",
)


class OsuBridgeError(RuntimeError):
    pass


def _base_url() -> str:
    return (os.getenv(TOSU_BASE_URL_ENV) or TOSU_BASE_URL_DEFAULT).rstrip("/")


def _state_path() -> Path:
    return Path(os.getenv(OSU_STATE_PATH_ENV) or OSU_STATE_PATH_DEFAULT)


def _http_get(path: str, *, timeout_s: float = 0.8) -> tuple[int, bytes, str]:
    url = path if path.startswith(("http://", "https://")) else urljoin(_base_url() + "/", path.lstrip("/"))
    request = Request(url, headers={"User-Agent": "NanaOsuBridge/0.1"})
    with urlopen(request, timeout=timeout_s) as response:
        return response.status, response.read(), response.headers.get("content-type", "")


def _safe_get_json(path: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        status, payload, _ = _http_get(path)
    except (OSError, URLError, TimeoutError) as exc:
        return None, f"{path}:{type(exc).__name__}"
    if status >= 400:
        return None, f"{path}:http_{status}"
    try:
        return json.loads(payload.decode("utf-8", errors="replace")), None
    except json.JSONDecodeError:
        return None, f"{path}:json_decode_failed"


def _dig(data: Any, *paths: str) -> Any:
    for path in paths:
        cur = data
        ok = True
        for part in path.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                ok = False
                break
        if ok and cur not in (None, ""):
            return cur
    return None


def _current_time_ms(data: dict[str, Any]) -> int | None:
    value = _dig(
        data,
        "beatmap.time.live",
        "play.time.current",
        "play.time.live",
        "session.playTime",
        "menu.bm.time.current",
        "menu.time.current",
        "gameplay.time.current",
        "time.current",
        "current_time",
        "currentTime",
    )
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _beatmap_info(data: dict[str, Any]) -> dict[str, Any]:
    beatmap = _dig(data, "menu.bm", "beatmap", "currentBeatmap") or {}
    metadata = _dig(data, "menu.bm.metadata", "beatmap.metadata", "metadata") or {}
    path = _dig(data, "menu.bm.path.full", "menu.bm.path.file", "beatmap.path", "path.full", "path")
    folder = _dig(data, "menu.bm.path.folder", "beatmap.folder", "folder")
    file_name = _dig(data, "menu.bm.path.file", "beatmap.file", "file")
    return {
        "raw": beatmap,
        "artist": _dig(metadata, "artist", "artistUnicode"),
        "title": _dig(metadata, "title", "titleUnicode"),
        "version": _dig(metadata, "difficulty", "version"),
        "creator": _dig(metadata, "mapper", "creator"),
        "path": path,
        "folder": folder,
        "file": file_name,
        "id": _dig(beatmap, "id", "beatmap_id"),
        "set_id": _dig(beatmap, "set", "set_id", "beatmapset_id"),
    }


def read_tosu_state() -> dict[str, Any]:
    errors = []
    for endpoint in CURRENT_BEATMAP_ENDPOINTS:
        data, error = _safe_get_json(endpoint)
        if data is not None:
            return {
                "ok": True,
                "source": f"tosu:{endpoint}",
                "base_url": _base_url(),
                "current_time_ms": _current_time_ms(data),
                "beatmap": _beatmap_info(data),
                "raw": data,
                "errors": errors,
            }
        errors.append(error)
    return {
        "ok": False,
        "source": "tosu",
        "base_url": _base_url(),
        "current_time_ms": None,
        "beatmap": {},
        "raw": None,
        "errors": [error for error in errors if error],
    }


def _read_local_current_map() -> tuple[str, str] | None:
    path = os.getenv(OSU_CURRENT_MAP_PATH_ENV)
    if not path:
        return None
    map_path = Path(path)
    if not map_path.exists():
        raise OsuBridgeError(f"current_map_missing:{map_path}")
    return map_path.read_text(encoding="utf-8-sig", errors="replace"), str(map_path)


def _read_tosu_current_map(state: dict[str, Any]) -> tuple[str, str] | None:
    beatmap = state.get("beatmap") or {}
    path = beatmap.get("path")
    if path:
        local_path = Path(str(path))
        if local_path.exists():
            return local_path.read_text(encoding="utf-8-sig", errors="replace"), str(local_path)

    endpoints = list(CURRENT_BEATMAP_FILE_ENDPOINTS)
    for endpoint in endpoints:
        if "{path}" in endpoint:
            if not path:
                continue
            endpoint = endpoint.format(path=path)
        try:
            status, payload, _ = _http_get(endpoint, timeout_s=1.5)
        except (OSError, URLError, TimeoutError):
            continue
        if status < 400 and payload:
            return payload.decode("utf-8-sig", errors="replace"), f"tosu:{endpoint}"
    return None


def read_current_map_text(state: dict[str, Any] | None = None) -> tuple[str | None, str | None, str | None]:
    try:
        local = _read_local_current_map()
        if local:
            return local[0], local[1], None
    except OsuBridgeError as exc:
        return None, None, str(exc)

    state = state or read_tosu_state()
    try:
        current = _read_tosu_current_map(state)
        if current:
            return current[0], current[1], None
    except OsuBridgeError as exc:
        return None, None, str(exc)
    return None, None, "current_map_unavailable"


def write_state(payload: dict[str, Any]) -> Path:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload)
    payload["written_at"] = time.time()
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
