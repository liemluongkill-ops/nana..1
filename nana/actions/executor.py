import json
import urllib.request

from nana.config import BROWSER_CDP_ENDPOINTS


class ActionExecutor:
    def __init__(self, endpoints=None, timeout=0.8):
        self.endpoints = endpoints or BROWSER_CDP_ENDPOINTS
        self.timeout = timeout
        self.request_id = 1000

    def execute(self, pending_action, current_context):
        if pending_action.action == "browser.click":
            return self._result("skipped", "executor_not_enabled_for_click")
        if pending_action.action == "browser.type":
            return self._result("skipped", "executor_not_enabled_for_type")
        if pending_action.action == "social.type_draft":
            reason = self._validate_final(pending_action, current_context)
            if reason:
                return self._result("skipped", reason)
            return self._type_social_draft(pending_action)
        if pending_action.action != "browser.scroll":
            return self._result("skipped", "unsupported_action")

        reason = self._validate_final(pending_action, current_context)
        if reason:
            return self._result("skipped", reason)

        return self._scroll_active_page(pending_action)

    def _validate_final(self, pending_action, current_context):
        if pending_action.action == "social.type_draft":
            return self._validate_social_final(pending_action, current_context)

        proposed = pending_action.context or {}
        if not current_context.get("browser_available"):
            return "browser_unavailable"
        if not current_context.get("browser_fresh"):
            return "browser_snapshot_stale"
        if not current_context.get("active_app_is_edge"):
            return "active_window_not_edge"
        if not current_context.get("active_window_valid"):
            return "active_window_not_validated"

        proposed_url = proposed.get("browser_url")
        current_url = current_context.get("browser_url")
        if proposed_url and current_url and proposed_url != current_url:
            return "browser_url_changed"
        return None

    def _validate_social_final(self, pending_action, current_context):
        proposed = pending_action.context or {}
        if not current_context.get("browser_available"):
            return "browser_unavailable"
        if not current_context.get("browser_fresh"):
            return "browser_snapshot_stale"

        proposed_url = proposed.get("browser_url")
        current_url = current_context.get("browser_url")
        if not current_url:
            return "browser_url_missing"
        if proposed_url and not self._same_social_page(proposed_url, current_url):
            return "browser_url_changed"

        social_reason = self._validate_social_target(current_context)
        if social_reason:
            return social_reason
        return None

    @staticmethod
    def _validate_social_target(current_context):
        url = (current_context.get("browser_url") or "").lower()
        kind = (current_context.get("browser_kind") or "").lower()
        social_domains = (
            "https://x.com/",
            "https://twitter.com/",
            "https://www.facebook.com/",
            "https://facebook.com/",
            "https://www.messenger.com/",
            "https://messenger.com/",
        )
        if not url:
            return "browser_url_missing"
        if kind != "social" and not url.startswith(social_domains):
            return "target_not_social_tab"
        return None

    def _scroll_active_page(self, pending_action):
        target_url = (pending_action.context or {}).get("browser_url")
        page, reason = self._active_page(
            target_url=target_url,
            require_target=bool(target_url),
            allow_same_social_host=True,
        )
        if not page:
            return self._result("skipped", reason)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            return self._result("skipped", "no_websocket_debugger_url")

        try:
            import websocket
        except Exception:
            return self._result("skipped", "websocket_client_not_installed")

        script = "window.scrollBy({top: Math.floor(window.innerHeight * 0.65), left: 0, behavior: 'smooth'}); 'ok';"
        try:
            ws = websocket.create_connection(
                websocket_url,
                timeout=self.timeout,
                suppress_origin=True,
            )
            try:
                request_id = self._next_request_id()
                ws.send(json.dumps({
                    "id": request_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": script,
                        "returnByValue": True,
                    },
                }))
                payload = self._recv_response(ws, request_id)
            finally:
                ws.close()

            if payload.get("error"):
                return self._result("skipped", "runtime_error")
            return self._result("executed", "scroll_ok")
        except Exception as exc:
            return self._result("skipped", f"executor_exception:{type(exc).__name__}")

    def _type_social_draft(self, pending_action):
        draft_text = (pending_action.context or {}).get("draft_text") or ""
        if not draft_text.strip():
            return self._result("skipped", "draft_text_empty")

        page, reason = self._active_page()
        if not page:
            return self._result("skipped", reason)
        websocket_url = page.get("webSocketDebuggerUrl")
        if not websocket_url:
            return self._result("skipped", "no_websocket_debugger_url")

        try:
            import websocket
        except Exception:
            return self._result("skipped", "websocket_client_not_installed")

        try:
            ws = websocket.create_connection(
                websocket_url,
                timeout=self.timeout,
                suppress_origin=True,
            )
            try:
                guard_id = self._next_request_id()
                ws.send(json.dumps({
                    "id": guard_id,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": self._focused_composer_guard_script(),
                        "returnByValue": True,
                    },
                }))
                guard_payload = self._recv_response(ws, guard_id)
                guard_value = (
                    guard_payload
                    .get("result", {})
                    .get("result", {})
                    .get("value")
                )
                if guard_value != "ok":
                    return self._result("skipped", guard_value or "focused_composer_not_ready")

                type_id = self._next_request_id()
                ws.send(json.dumps({
                    "id": type_id,
                    "method": "Input.insertText",
                    "params": {
                        "text": draft_text,
                    },
                }))
                type_payload = self._recv_response(ws, type_id)
            finally:
                ws.close()

            if type_payload.get("error"):
                return self._result("skipped", "insert_text_error")
            return self._result("executed", "social_type_ok")
        except Exception as exc:
            return self._result("skipped", f"executor_exception:{type(exc).__name__}")

    @staticmethod
    def _focused_composer_guard_script():
        return r"""
(() => {
  const active = document.activeElement;
  const selected = window.getSelection && window.getSelection();
  const selectedElement =
    selected && selected.anchorNode
      ? (selected.anchorNode.nodeType === Node.ELEMENT_NODE
          ? selected.anchorNode
          : selected.anchorNode.parentElement)
      : null;
  const candidate =
    closestEditable(active) ||
    closestEditable(selectedElement);
  if (!candidate) return "no_focused_composer";

  const tag = (candidate.tagName || "").toLowerCase();
  const role = (candidate.getAttribute("role") || "").toLowerCase();
  const type = (candidate.getAttribute("type") || "").toLowerCase();
  const aria = (candidate.getAttribute("aria-label") || "").toLowerCase();
  const placeholder = (candidate.getAttribute("placeholder") || "").toLowerCase();
  const editable = candidate.isContentEditable || candidate.getAttribute("contenteditable") === "true";
  const textBits = `${aria} ${placeholder}`;

  if (type === "password") return "focused_password_field";
  if (textBits.match(/search|tìm kiếm|login|log in|email|phone|password|mật khẩu|username/)) {
    return "focused_field_not_social_composer";
  }

  const looksEditable =
    editable ||
    tag === "textarea" ||
    (tag === "input" && ["", "text", "search"].includes(type)) ||
    role === "textbox";
  if (!looksEditable) return "focused_element_not_editable";

  const looksComposer =
    editable ||
    tag === "textarea" ||
    textBits.match(/post|tweet|reply|comment|message|write|compose|what is happening|what's happening|your reply|add a comment|bình luận|trả lời|viết|tin nhắn|đăng/);
  if (!looksComposer) return "focused_field_not_social_composer";

  candidate.focus();
  return "ok";

  function closestEditable(node) {
    if (!node || !node.closest) return null;
    return node.closest('[contenteditable="true"], textarea, input, [role="textbox"]');
  }
})()
"""

    def _active_page(self, target_url=None, require_target=False, allow_same_social_host=False):
        last_reason = "debug_port_unavailable"
        normalized_target_url = self._normalize_url(target_url)
        for browser_name, endpoint in self.endpoints:
            try:
                with urllib.request.urlopen(endpoint, timeout=self.timeout) as response:
                    pages = json.loads(response.read().decode("utf-8", errors="replace"))
            except Exception as exc:
                last_reason = f"{browser_name}: {type(exc).__name__}"
                continue

            candidates = [
                page for page in pages
                if page.get("type") == "page"
                and page.get("url")
                and not page.get("url", "").startswith(("devtools://", "chrome://", "edge://"))
            ]
            if candidates:
                if normalized_target_url:
                    for candidate in candidates:
                        candidate_url = candidate.get("url")
                        if (
                            self._normalize_url(candidate_url) == normalized_target_url
                            or (
                                allow_same_social_host
                                and self._same_social_page(target_url, candidate_url)
                            )
                        ):
                            return candidate, "ok"
                    if require_target:
                        last_reason = f"{browser_name}: target_page_not_found"
                        continue
                return candidates[0], "ok"
            last_reason = f"{browser_name}: no_page"
        return None, last_reason

    @staticmethod
    def _normalize_url(url):
        return (url or "").rstrip("/")

    @classmethod
    def _same_social_page(cls, proposed_url, current_url):
        proposed = cls._social_host(proposed_url)
        current = cls._social_host(current_url)
        return bool(proposed and current and proposed == current)

    @staticmethod
    def _social_host(url):
        value = (url or "").lower()
        for host in ("x.com", "twitter.com", "www.facebook.com", "facebook.com", "www.messenger.com", "messenger.com"):
            if value.startswith(f"https://{host}/") or value == f"https://{host}":
                if host == "twitter.com":
                    return "x.com"
                if host == "facebook.com":
                    return "www.facebook.com"
                if host == "messenger.com":
                    return "www.messenger.com"
                return host
        return ""

    def _next_request_id(self):
        self.request_id += 1
        return self.request_id

    def _recv_response(self, ws, request_id, max_messages=12):
        for _ in range(max_messages):
            payload = json.loads(ws.recv())
            if payload.get("id") == request_id:
                return payload
        raise TimeoutError("cdp_response_not_found")

    @staticmethod
    def _result(status, reason):
        return {
            "status": status,
            "reason": reason,
        }


action_executor = ActionExecutor()
