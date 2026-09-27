import json
import base64
from collections import deque
import math
import os
from pathlib import Path
import threading
import time

import requests

from nana.config import (
    LLMGATE_CHEAP_MODEL,
    LLMGATE_MAIN_MODEL,
    LLMGATE_MAIN_REASONING_EFFORT,
    LLMGATE_PUBLIC_MODEL,
    LLMGATE_SETTINGS_PATH,
    LLMGATE_TIMEOUT,
)
from nana.runtime.logger import log_event


_VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_HTTP_LOCAL = threading.local()
_TRANSPORT_LOCK = threading.Lock()
_TRANSPORT_HISTORY = deque(maxlen=50)
_LAST_TRANSPORT = {
    "sequence": 0,
    "model": "none",
    "stream": False,
    "status": "idle",
    "pooled": True,
    "prompt_chars": 0,
    "max_tokens": 0,
    "first_text_ms": None,
    "tail_after_first_ms": None,
    "total_ms": None,
    "chunks": 0,
    "output_chars": 0,
    "error": "none",
}


def _http_session():
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=4,
            pool_maxsize=8,
            max_retries=0,
            pool_block=False,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _HTTP_LOCAL.session = session
    return session


def _http_post(url, **kwargs):
    return _http_session().post(url, **kwargs)


def _content_char_count(content):
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(_content_char_count(item) for item in content)
    if isinstance(content, dict):
        if content.get("type") == "image_url":
            return 0
        return sum(_content_char_count(value) for value in content.values())
    return 0


def _message_char_count(messages):
    return sum(
        _content_char_count(message.get("content"))
        for message in (messages or [])
        if isinstance(message, dict)
    )


def _record_transport(
    *,
    model_name,
    payload,
    stream,
    status,
    started,
    first_text_ms=None,
    chunks=0,
    output_chars=0,
    error="none",
):
    total_ms = (time.perf_counter() - started) * 1000.0
    tail_after_first_ms = None
    if first_text_ms is not None:
        tail_after_first_ms = max(0.0, total_ms - float(first_text_ms))
    with _TRANSPORT_LOCK:
        sequence = int(_LAST_TRANSPORT.get("sequence") or 0) + 1
        event = {
            "sequence": sequence,
            "model": str(payload.get("model") or model_name or "none"),
            "stream": bool(stream),
            "status": str(status or "unknown"),
            "pooled": True,
            "prompt_chars": _message_char_count(payload.get("messages")),
            "max_tokens": int(payload.get("max_tokens") or 0),
            "first_text_ms": (
                round(float(first_text_ms), 1)
                if first_text_ms is not None
                else None
            ),
            "tail_after_first_ms": (
                round(tail_after_first_ms, 1)
                if tail_after_first_ms is not None
                else None
            ),
            "total_ms": round(total_ms, 1),
            "chunks": int(chunks or 0),
            "output_chars": int(output_chars or 0),
            "error": str(error or "none")[:160],
        }
        _LAST_TRANSPORT.update(event)
        _TRANSPORT_HISTORY.append(dict(event))


def llmgate_transport_snapshot():
    with _TRANSPORT_LOCK:
        return dict(_LAST_TRANSPORT)


def _latency_summary(events, key):
    values = sorted(
        float(event[key])
        for event in events
        if event.get(key) is not None
    )
    if not values:
        return {
            "count": 0,
            "min_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "max_ms": None,
        }
    midpoint = len(values) // 2
    if len(values) % 2:
        p50 = values[midpoint]
    else:
        p50 = (values[midpoint - 1] + values[midpoint]) / 2.0
    p95_index = max(0, math.ceil(0.95 * len(values)) - 1)
    return {
        "count": len(values),
        "min_ms": round(values[0], 1),
        "p50_ms": round(p50, 1),
        "p95_ms": round(values[p95_index], 1),
        "max_ms": round(values[-1], 1),
    }


def llmgate_transport_stats(*, model_name=None, stream=None, limit=20):
    with _TRANSPORT_LOCK:
        history = list(_TRANSPORT_HISTORY)
    filtered = []
    for event in history:
        if event.get("status") != "complete":
            continue
        if model_name and event.get("model") != model_name:
            continue
        if stream is not None and bool(event.get("stream")) is not bool(stream):
            continue
        filtered.append(event)
    bounded = filtered[-max(1, int(limit)) :]
    return {
        "model": model_name or "all",
        "stream": stream,
        "window": len(bounded),
        "first_text": _latency_summary(bounded, "first_text_ms"),
        "tail_after_first": _latency_summary(bounded, "tail_after_first_ms"),
        "total": _latency_summary(bounded, "total_ms"),
    }


def resolve_llmgate_reasoning_effort(model_name, requested=None):
    """Resolve an optional Chat Completions reasoning effort for one model.

    The latency-sensitive private main gets an explicit effort only when its
    resolved payload model is GPT-5.6. Existing public, cheap, reasoning,
    refiner, social, and vision models retain their provider defaults.
    """
    if requested is None:
        resolved_model = (_candidate_model_names(model_name) or [model_name])[-1]
        resolved_main = (_candidate_model_names(LLMGATE_MAIN_MODEL) or [LLMGATE_MAIN_MODEL])[-1]
        if resolved_model != resolved_main or not str(resolved_model).startswith("gpt-5.6"):
            return None
        requested = LLMGATE_MAIN_REASONING_EFFORT

    effort = str(requested or "").strip().lower()
    return effort if effort in _VALID_REASONING_EFFORTS else None


def apply_llmgate_reasoning_effort(payload, model_name, requested=None):
    effort = resolve_llmgate_reasoning_effort(model_name, requested=requested)
    if effort:
        payload["reasoning_effort"] = effort
    return effort


def call_llmgate(model_name, prompt, max_tokens=180, temperature=0.2, reasoning_effort=None):
    model_config = load_llmgate_model(model_name)
    if not model_config:
        return None, f"model_not_configured: {model_name}"

    api_key = model_config.get("apiKey")
    base_url = (model_config.get("baseUrl") or "").rstrip("/")
    if not api_key:
        return None, "missing_api_key"
    if not base_url:
        return None, "missing_base_url"

    payload = {
        "model": model_config.get("model") or model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Bạn là worker phụ của Nana. Trả lời ngắn, đúng yêu cầu. "
                    "Không thêm dữ kiện mới nếu prompt không cung cấp."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    apply_llmgate_reasoning_effort(payload, model_name, requested=reasoning_effort)
    started = time.perf_counter()

    try:
        response = _http_post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=LLMGATE_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _record_transport(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="complete",
            started=started,
            first_text_ms=elapsed_ms,
            chunks=1,
            output_chars=len(content),
        )
        log_event("runtime", f"LLMGate sidecar used: {model_name}")
        return content, "ok"
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            body = (exc.response.text or "").strip().replace("\n", " ")
        debug = f"http_error: {status} | {body[:180] or 'no_body'}"
        _record_transport(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="failed",
            started=started,
            error=debug,
        )
        return None, debug
    except Exception as exc:
        debug = f"client_exception: {type(exc).__name__}"
        _record_transport(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="failed",
            started=started,
            error=debug,
        )
        return None, debug


def call_llmgate_messages(model_name, messages, max_tokens=180, temperature=0.2, reasoning_effort=None, *, timeout_s=None):
    model_config = load_llmgate_model(model_name)
    if not model_config:
        return None, f"model_not_configured: {model_name}"

    api_key = model_config.get("apiKey")
    base_url = (model_config.get("baseUrl") or "").rstrip("/")
    if not api_key:
        return None, "missing_api_key"
    if not base_url:
        return None, "missing_base_url"

    payload = {
        "model": model_config.get("model") or model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    apply_llmgate_reasoning_effort(payload, model_name, requested=reasoning_effort)
    started = time.perf_counter()
    request_timeout = LLMGATE_TIMEOUT if timeout_s is None else min(LLMGATE_TIMEOUT, max(.1, float(timeout_s)))

    try:
        response = _http_post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=request_timeout,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        _record_transport(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="complete",
            started=started,
            first_text_ms=elapsed_ms,
            chunks=1,
            output_chars=len(content),
        )
        log_event("runtime", f"LLMGate chat used: {model_name}")
        return content, "ok"
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            body = (exc.response.text or "").strip().replace("\n", " ")
        debug = f"http_error: {status} | {body[:180] or 'no_body'}"
        _record_transport(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="failed",
            started=started,
            error=debug,
        )
        return None, debug
    except Exception as exc:
        debug = f"client_exception: {type(exc).__name__}"
        _record_transport(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="failed",
            started=started,
            error=debug,
        )
        return None, debug


def call_llmgate_vision(model_name, prompt, image_path, max_tokens=120, temperature=0.1):
    model_config = load_llmgate_model(model_name)
    if not model_config:
        return None, f"model_not_configured: {model_name}"

    api_key = model_config.get("apiKey")
    base_url = (model_config.get("baseUrl") or "").rstrip("/")
    if not api_key:
        return None, "missing_api_key"
    if not base_url:
        return None, "missing_base_url"

    path = Path(image_path)
    if not path.exists():
        return None, "image_not_found"
    try:
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    except Exception:
        return None, "image_read_failed"

    payload = {
        "model": model_config.get("model") or model_name,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Bạn là worker Vision phụ của Nana. Chỉ mô tả những gì nhìn thấy trong ảnh. "
                    "Trả lời tiếng Việt, 1-2 dòng, không suy đoán danh tính, không thêm hành động."
                ),
            },
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{encoded}",
                        },
                    },
                ],
            },
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
    }

    try:
        response = _http_post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=LLMGATE_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"].strip()
        log_event("runtime", f"LLMGate vision sidecar used: {model_name}")
        return content, "ok"
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            body = (exc.response.text or "").strip().replace("\n", " ")
        return None, f"http_error: {status} | {body[:180] or 'no_body'}"
    except Exception as exc:
        return None, f"client_exception: {type(exc).__name__}"


def load_llmgate_model(model_name):
    settings = load_llmgate_settings()
    if not settings:
        return None
    for candidate_name in _candidate_model_names(model_name):
        for model in settings.get("customModels", []):
            if (
                model.get("model") == candidate_name
                or model.get("displayName") == candidate_name
            ):
                return resolve_model_env_placeholders(model)
    target_name = (_candidate_model_names(model_name) or [model_name])[-1]
    return clone_llmgate_base_model(settings, target_name)


def clone_llmgate_base_model(settings, model_name):
    """Reuse an existing LLMGate endpoint/key for dashboard model IDs.

    Some LLMGate models are visible in the dashboard but not listed in Codex's
    custom model settings. When Nana explicitly asks for one of those IDs, use
    the same LLMGate endpoint credentials and only swap the payload model name.
    """
    target = str(model_name or "").strip()
    if not target:
        return None
    for model in settings.get("customModels", []):
        resolved = resolve_model_env_placeholders(model)
        if resolved.get("apiKey") and resolved.get("baseUrl"):
            clone = dict(resolved)
            clone["model"] = target
            clone["displayName"] = target
            return clone
    return None


def _candidate_model_names(model_name):
    raw = str(model_name or "").strip()
    if not raw:
        return []
    alias = _resolve_model_alias(raw)
    names = [raw]
    if alias and alias not in names:
        names.append(alias)
    return names


def _resolve_model_alias(model_name):
    """Map Nana logical model names to configured LLMGate model IDs."""
    aliases = {
        "nana-main": os.getenv("NANA_LLM_ALIAS_MAIN", LLMGATE_MAIN_MODEL),
        "nana-chat": os.getenv("NANA_LLM_ALIAS_CHAT", LLMGATE_MAIN_MODEL),
        "nana-public": os.getenv("NANA_LLM_ALIAS_PUBLIC", LLMGATE_PUBLIC_MODEL),
        "nana-cheap": os.getenv("NANA_LLM_ALIAS_CHEAP", LLMGATE_CHEAP_MODEL),
        "nana-banter": os.getenv("NANA_LLM_ALIAS_BANTER", LLMGATE_CHEAP_MODEL),
        "nana-refiner": os.getenv("NANA_LLM_ALIAS_REFINER", "gpt-5.5"),
        "nana-reasoning": os.getenv("NANA_LLM_ALIAS_REASONING", "gpt-5.4"),
    }
    return aliases.get(str(model_name or "").strip(), model_name)


def resolve_model_env_placeholders(model):
    """Return a copy with ${ENV_NAME} string values expanded from env."""
    if not isinstance(model, dict):
        return model
    resolved = dict(model)
    for key, value in list(resolved.items()):
        if (
            isinstance(value, str)
            and value.startswith("${")
            and value.endswith("}")
            and len(value) > 3
        ):
            env_name = value[2:-1].strip()
            if env_name:
                resolved[key] = os.getenv(env_name, value)
    return resolved


def load_llmgate_settings():
    path = Path(LLMGATE_SETTINGS_PATH)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return None


def stream_llmgate_messages(model_name, messages, max_tokens=1000, temperature=0.75, reasoning_effort=None):
    """Stream LLM response via SSE. Yields text chunks as they arrive."""
    model_config = load_llmgate_model(model_name)
    if not model_config:
        return
    api_key = model_config.get("apiKey")
    base_url = (model_config.get("baseUrl") or "").rstrip("/")
    if not api_key or not base_url:
        return

    payload = {
        "model": model_config.get("model") or model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    apply_llmgate_reasoning_effort(payload, model_name, requested=reasoning_effort)
    started = time.perf_counter()
    first_text_ms = None
    chunks = 0
    output_chars = 0
    status = "aborted"
    error = "none"

    try:
        import sseclient
        response = _http_post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=LLMGATE_TIMEOUT,
            stream=True,
        )
        response.raise_for_status()
        client = sseclient.SSEClient(response)
        for event in client.events():
            if event.data and event.data.strip() and event.data != "[DONE]":
                try:
                    obj = json.loads(event.data)
                    delta = obj.get("choices", [{}])[0].get("delta", {})
                    content = delta.get("content", "")
                    if content:
                        if first_text_ms is None:
                            first_text_ms = (
                                time.perf_counter() - started
                            ) * 1000.0
                        chunks += 1
                        output_chars += len(content)
                        yield content
                except (json.JSONDecodeError, IndexError, KeyError):
                    continue
        status = "complete"
        log_event("runtime", f"LLMGate stream used: {model_name}")
    except Exception as exc:
        status = "failed"
        error = f"{type(exc).__name__}: {exc}"
        log_event("errors", f"LLMGate stream failed: {exc}")
    finally:
        _record_transport(
            model_name=model_name,
            payload=payload,
            stream=True,
            status=status,
            started=started,
            first_text_ms=first_text_ms,
            chunks=chunks,
            output_chars=output_chars,
            error=error,
        )
