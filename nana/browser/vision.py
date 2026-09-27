import base64
import json
import time
import urllib.request
from pathlib import Path

from nana.actions.privacy import build_context_budget_preview
from nana.config import BROWSER_CDP_ENDPOINTS, DATA_DIR


class VisionPreviewer:
    def __init__(self, endpoints=None, timeout=1.0, output_dir=None):
        self.endpoints = endpoints or BROWSER_CDP_ENDPOINTS
        self.timeout = timeout
        self.output_dir = Path(output_dir or (DATA_DIR / "vision_preview"))
        self.request_id = 3000

    def capture_focus(self, context):
        self.cleanup_preview_cache()
        context_preview = build_context_budget_preview(context, level="L1")
        if not context_preview.allowed_for_external_model:
            return {
                "status": "blocked",
                "reason": "privacy_gate_blocked",
                "privacy_risk": context_preview.risk,
                "blocked_reasons": context_preview.blocked_reasons,
            }

        page, reason = self._active_page(context.get("browser_url"))
        if not page:
            return {
                "status": "skipped",
                "reason": reason,
                "privacy_risk": context_preview.risk,
                "blocked_reasons": [],
            }

        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            return {
                "status": "skipped",
                "reason": "no_websocket_debugger_url",
                "privacy_risk": context_preview.risk,
                "blocked_reasons": [],
            }

        try:
            import websocket
        except Exception:
            return {
                "status": "skipped",
                "reason": "websocket_client_not_installed",
                "privacy_risk": context_preview.risk,
                "blocked_reasons": [],
            }

        try:
            ws = websocket.create_connection(
                websocket_url,
                timeout=self.timeout,
                suppress_origin=True,
            )
            try:
                clip_info = self._evaluate_focus_clip(ws)
                if clip_info.get("status") != "ok":
                    return {
                        "status": "skipped",
                        "reason": clip_info.get("reason", "clip_unavailable"),
                        "privacy_risk": context_preview.risk,
                        "blocked_reasons": [],
                    }
                if self._clip_too_small(clip_info.get("clip") or {}):
                    return {
                        "status": "skipped",
                        "reason": "clip_too_small",
                        "privacy_risk": context_preview.risk,
                        "blocked_reasons": [],
                        "clip": clip_info.get("clip"),
                        "target": clip_info.get("target", "unknown"),
                    }
                png_bytes = self._capture_clip(ws, clip_info["clip"])
            finally:
                ws.close()
        except Exception as exc:
            return {
                "status": "skipped",
                "reason": f"vision_exception:{type(exc).__name__}",
                "privacy_risk": context_preview.risk,
                "blocked_reasons": [],
            }

        self.output_dir.mkdir(parents=True, exist_ok=True)
        path = self.output_dir / f"vision_focus_{int(time.time())}.png"
        path.write_bytes(png_bytes)
        return {
            "status": "captured",
            "reason": "focus_crop_ok",
            "privacy_risk": context_preview.risk,
            "blocked_reasons": [],
            "path": str(path),
            "clip": clip_info["clip"],
            "target": clip_info.get("target", "unknown"),
            "note": "local_preview_only_no_model_upload",
        }

    def latest_preview(self):
        if not self.output_dir.exists():
            return None
        files = sorted(
            self.output_dir.glob("vision_focus_*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        return files[0] if files else None

    def cleanup_preview_cache(self, max_age_seconds=1800, keep_latest=20):
        if not self.output_dir.exists():
            return 0
        now = time.time()
        files = sorted(
            self.output_dir.glob("vision_focus_*.png"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for index, path in enumerate(files):
            try:
                age = now - path.stat().st_mtime
                if index >= keep_latest or age > max_age_seconds:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def clear_preview_cache(self):
        if not self.output_dir.exists():
            return 0
        removed = 0
        for path in self.output_dir.glob("vision_focus_*.png"):
            try:
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def _evaluate_focus_clip(self, ws):
        request_id = self._next_request_id()
        ws.send(json.dumps({
            "id": request_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": self._focus_clip_script(),
                "returnByValue": True,
            },
        }))
        payload = self._recv_response(ws, request_id)
        if payload.get("error"):
            return {"status": "skipped", "reason": "runtime_error"}
        value = payload.get("result", {}).get("result", {}).get("value")
        if not value:
            return {"status": "skipped", "reason": "empty_clip"}
        try:
            return json.loads(value)
        except Exception:
            return {"status": "skipped", "reason": "invalid_clip_json"}

    def _capture_clip(self, ws, clip):
        request_id = self._next_request_id()
        ws.send(json.dumps({
            "id": request_id,
            "method": "Page.captureScreenshot",
            "params": {
                "format": "png",
                "captureBeyondViewport": False,
                "fromSurface": True,
                "clip": clip,
            },
        }))
        payload = self._recv_response(ws, request_id)
        if payload.get("error"):
            raise RuntimeError("capture_screenshot_error")
        data = payload.get("result", {}).get("data")
        if not data:
            raise RuntimeError("capture_screenshot_empty")
        return base64.b64decode(data)

    @staticmethod
    def _clip_too_small(clip, min_width=120, min_height=80):
        try:
            width = float(clip.get("width") or 0)
            height = float(clip.get("height") or 0)
        except (TypeError, ValueError):
            return True
        return width < min_width or height < min_height

    @staticmethod
    def _focus_clip_script():
        return r"""
(() => {
  const candidates = [
    ...Array.from(document.querySelectorAll('article')),
    ...Array.from(document.querySelectorAll('[role="article"]')),
    document.querySelector('main video'),
    document.querySelector('video'),
    document.querySelector('[role="main"]'),
    document.querySelector('main')
  ].filter(Boolean);
  const target = candidates.find((node) => {
    const rect = node.getBoundingClientRect();
    const visible = rect.bottom > 0 && rect.right > 0 && rect.top < window.innerHeight && rect.left < window.innerWidth;
    return visible && rect.width >= 180 && rect.height >= 120;
  });
  if (!target) return JSON.stringify({status: "skipped", reason: "no_focus_target"});

  const rect = target.getBoundingClientRect();
  const viewportW = Math.max(document.documentElement.clientWidth || 0, window.innerWidth || 0);
  const viewportH = Math.max(document.documentElement.clientHeight || 0, window.innerHeight || 0);
  const pad = 8;
  const x = Math.max(0, Math.floor(rect.left - pad));
  const y = Math.max(0, Math.floor(rect.top - pad));
  const right = Math.min(viewportW, Math.ceil(rect.right + pad));
  const bottom = Math.min(viewportH, Math.ceil(rect.bottom + pad));
  const width = Math.max(1, Math.min(900, right - x));
  const height = Math.max(1, Math.min(900, bottom - y));
  const scale = window.devicePixelRatio || 1;
  return JSON.stringify({
    status: "ok",
    target: (target.tagName || "unknown").toLowerCase(),
    clip: {x, y, width, height, scale}
  });
})()
"""

    def _active_page(self, target_url=None):
        for _, endpoint in self.endpoints:
            try:
                with urllib.request.urlopen(endpoint, timeout=self.timeout) as response:
                    pages = json.loads(response.read().decode("utf-8", errors="replace"))
            except Exception as exc:
                return None, f"debug_port_unavailable:{type(exc).__name__}"

            candidates = [
                page for page in pages
                if page.get("type") == "page"
                and page.get("url")
                and not page.get("url", "").startswith(("devtools://", "chrome://", "edge://"))
            ]
            if not candidates:
                continue
            if target_url:
                for page in candidates:
                    if page.get("url") == target_url:
                        return page, "ok"
            return candidates[0], "ok"
        return None, "no_page"

    def _next_request_id(self):
        self.request_id += 1
        return self.request_id

    @staticmethod
    def _recv_response(ws, request_id, max_messages=20):
        for _ in range(max_messages):
            payload = json.loads(ws.recv())
            if payload.get("id") == request_id:
                return payload
        raise TimeoutError("cdp_response_not_found")
