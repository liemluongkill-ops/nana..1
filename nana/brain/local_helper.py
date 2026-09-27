import json

import requests

from nana.config import OLLAMA_MODEL, OLLAMA_TIMEOUT, OLLAMA_URL
from nana.runtime.logger import log_event


def summarize_browser_focus(browser):
    if not browser or not browser.get("available"):
        return None, "browser_unavailable"

    parts = []
    if browser.get("page_heading"):
        parts.append(f"Heading: {browser['page_heading']}")
    if browser.get("meta_description"):
        parts.append(f"Meta: {browser['meta_description']}")
    if browser.get("selected_text"):
        parts.append(f"Selected: {browser['selected_text']}")
    if not parts:
        return None, "no_focus_fields"

    prompt = (
        "Tom tat browser context duoi day thanh 1 cau tieng Viet rat ngan. "
        "Chi tra ve noi dung tom tat, khong giai thich, khong them mo dau.\n\n"
        + "\n".join(parts)
    )

    payload = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_predict": 80,
        },
    }

    try:
        response = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        text = (data.get("response") or "").strip()
        if not text:
            return None, "empty_response"
        text = " ".join(text.split())
        log_event("runtime", f"Ollama helper used: {OLLAMA_MODEL}")
        return text[:220], "ok"
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            body = (exc.response.text or "").strip().replace("\n", " ")
        body = body[:160] if body else "no_body"
        log_event("runtime", f"Ollama helper HTTP error {status}: {body}")
        return None, f"helper_http_error: {status} | {body}"
    except Exception as exc:
        log_event("runtime", f"Ollama helper skipped: {exc}")
        return None, f"helper_exception: {type(exc).__name__}"


def apply_local_summary(snapshot):
    if not snapshot:
        return snapshot
    if not snapshot.available:
        snapshot.local_summary = None
        snapshot.local_helper_debug = "browser_unavailable"
        return snapshot
    try:
        summary, debug = summarize_browser_focus({
            "available": snapshot.available,
            "page_heading": snapshot.page_heading,
            "meta_description": snapshot.meta_description,
            "selected_text": snapshot.selected_text,
        })
    except Exception as exc:
        summary = None
        debug = f"helper_internal_error: {type(exc).__name__}"
        log_event("runtime", f"Ollama helper internal error: {exc}")
    snapshot.local_summary = summary
    snapshot.local_helper_debug = debug
    return snapshot
