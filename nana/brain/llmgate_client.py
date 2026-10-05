import json
import base64
from collections import deque
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import re
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
from nana.runtime.context_telemetry import (
    TransportReceipt,
    prepare_compiled_transport_messages,
    require_compiled_transport_integrity,
)
from nana.runtime.context_contracts import ContextContractError
from nana.runtime.context_readiness import (
    require_compiled_private_readiness,
    resolve_endpoint_identity_v1,
)


_VALID_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_SAFE_RECEIPT_METADATA = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,127}")
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
    "full_response_ms": None,
    "tail_after_first_ms": None,
    "total_ms": None,
    "chunks": 0,
    "output_chars": 0,
    "error": "none",
    "request_id": None,
    "correlation_id": None,
    "full_context_hash": None,
    "logical_model": None,
    "resolved_model": None,
    "observed_provider": None,
    "http_status": None,
    "provider_usage_present": False,
    "usage_status": "missing",
    "input_tokens": None,
    "cached_tokens": None,
    "cache_write_tokens": None,
    "output_tokens": None,
    "total_tokens": None,
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
    full_response_ms=None,
    total_ms=None,
    chunks=0,
    output_chars=0,
    error="none",
    receipt=None,
    usage=None,
    http_status=None,
    observed_provider=None,
):
    if total_ms is None:
        total_ms = (time.perf_counter() - started) * 1000.0
    tail_after_first_ms = None
    if first_text_ms is not None:
        tail_after_first_ms = max(0.0, total_ms - float(first_text_ms))
    with _TRANSPORT_LOCK:
        sequence = int(_LAST_TRANSPORT.get("sequence") or 0) + 1
        event = {
            "sequence": sequence,
            "model": _safe_receipt_metadata(
                payload.get("model") or model_name,
                fallback="invalid-model",
            ),
            "stream": bool(stream),
            "status": _safe_receipt_metadata(status, fallback="unknown"),
            "pooled": True,
            "prompt_chars": _message_char_count(payload.get("messages")),
            "max_tokens": int(payload.get("max_tokens") or 0),
            "first_text_ms": (
                round(float(first_text_ms), 1)
                if first_text_ms is not None
                else None
            ),
            "full_response_ms": (
                round(float(full_response_ms), 1)
                if full_response_ms is not None
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
            "error": _safe_receipt_metadata(error, fallback="invalid-error"),
        }
        if receipt is not None:
            event.update(
                {
                    "request_id": receipt.request_id,
                    "correlation_id": receipt.correlation_id,
                    "full_context_hash": receipt.full_context_hash,
                    "logical_model": receipt.logical_model,
                    "resolved_model": receipt.resolved_model,
                    "observed_provider": receipt.observed_provider,
                    "http_status": receipt.http_status,
                    "provider_usage_present": receipt.provider_usage_present,
                    "usage_status": receipt.usage_status,
                    "input_tokens": receipt.input_tokens,
                    "cached_tokens": receipt.cached_tokens,
                    "cache_write_tokens": receipt.cache_write_tokens,
                    "output_tokens": receipt.output_tokens,
                    "total_tokens": receipt.total_tokens,
                }
            )
        else:
            usage = usage or _UsageObservation()
            event.update(
                {
                    "request_id": None,
                    "correlation_id": None,
                    "full_context_hash": None,
                    "logical_model": None,
                    "resolved_model": None,
                    "observed_provider": _safe_receipt_metadata(observed_provider),
                    "http_status": (
                        http_status
                        if type(http_status) is int and 100 <= http_status <= 599
                        else None
                    ),
                    "provider_usage_present": bool(usage.present),
                    "usage_status": usage.status,
                    "input_tokens": usage.input_tokens,
                    "cached_tokens": usage.cached_tokens,
                    "cache_write_tokens": usage.cache_write_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                }
            )
        _LAST_TRANSPORT.update(event)
        _TRANSPORT_HISTORY.append(dict(event))


def _record_transport_best_effort(**kwargs):
    try:
        _record_transport(**kwargs)
    except Exception:
        pass


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


@dataclass(frozen=True, slots=True)
class _UsageObservation:
    present: bool = False
    status: str = "missing"
    input_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True, repr=False)
class _BoundLLMGateDispatch:
    route: object
    api_key: str
    base_url: str
    resolved_model: str


def _safe_receipt_metadata(value, *, fallback=None):
    if type(value) is str and _SAFE_RECEIPT_METADATA.fullmatch(value):
        return value
    return fallback


def _safe_receipt_identifier(value):
    safe = _safe_receipt_metadata(value)
    if safe is not None:
        return safe
    digest = hashlib.sha256(str(value).encode("utf-8", errors="replace")).hexdigest()
    return f"sha256:{digest}"


def _usage_alias(mapping, names):
    values = [mapping[name] for name in names if name in mapping]
    if not values:
        return None
    if any(type(value) is not int or value < 0 for value in values):
        raise ValueError("invalid_usage")
    if any(value != values[0] for value in values[1:]):
        raise ValueError("conflicting_usage")
    return values[0]


def _normalize_provider_usage(container):
    if type(container) is not dict or "usage" not in container or container["usage"] is None:
        return _UsageObservation()
    usage = container["usage"]
    if type(usage) is not dict:
        return _UsageObservation(present=True, status="unsupported")
    try:
        details = []
        for name in ("prompt_tokens_details", "input_tokens_details"):
            if name not in usage or usage[name] is None:
                continue
            if type(usage[name]) is not dict:
                raise ValueError("invalid_usage")
            details.append(usage[name])
        cached_candidates = []
        cache_write_candidates = []
        direct_cached = _usage_alias(usage, ("cached_tokens",))
        direct_write = _usage_alias(
            usage,
            ("cache_write_tokens", "cache_creation_input_tokens"),
        )
        if direct_cached is not None:
            cached_candidates.append(direct_cached)
        if direct_write is not None:
            cache_write_candidates.append(direct_write)
        for detail in details:
            cached = _usage_alias(detail, ("cached_tokens",))
            cache_write = _usage_alias(
                detail,
                ("cache_write_tokens", "cache_creation_input_tokens"),
            )
            if cached is not None:
                cached_candidates.append(cached)
            if cache_write is not None:
                cache_write_candidates.append(cache_write)
        if len(set(cached_candidates)) > 1 or len(set(cache_write_candidates)) > 1:
            raise ValueError("conflicting_usage")
        values = {
            "input_tokens": _usage_alias(usage, ("input_tokens", "prompt_tokens")),
            "cached_tokens": cached_candidates[0] if cached_candidates else None,
            "cache_write_tokens": (
                cache_write_candidates[0] if cache_write_candidates else None
            ),
            "output_tokens": _usage_alias(
                usage, ("output_tokens", "completion_tokens")
            ),
            "total_tokens": _usage_alias(usage, ("total_tokens",)),
        }
    except (TypeError, ValueError):
        return _UsageObservation(present=True, status="invalid")
    if all(value is None for value in values.values()):
        return _UsageObservation(present=True, status="unsupported")
    return _UsageObservation(present=True, status="valid", **values)


def _observed_provider(container):
    if type(container) is not dict:
        return None
    for key in ("provider", "provider_name"):
        value = _safe_receipt_metadata(container.get(key))
        if value is not None:
            return value
    return None


def _best_effort_log(channel, message):
    try:
        log_event(channel, message)
    except Exception:
        pass


def _best_effort_observe(observer, receipt):
    if observer is None:
        return
    try:
        observer(receipt)
    except Exception:
        pass


def _best_effort_close(response):
    if response is None:
        return
    try:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    except Exception:
        pass


def _make_transport_receipt(
    compiled,
    *,
    resolved_model,
    observed_provider,
    stream,
    status,
    error_code,
    http_status,
    first_text_ms,
    full_response_ms,
    total_ms,
    output_chars,
    chunk_count,
    usage,
):
    manifest = compiled.manifest
    return TransportReceipt(
        request_id=_safe_receipt_identifier(manifest.request_id),
        correlation_id=_safe_receipt_identifier(manifest.correlation_id),
        full_context_hash=compiled.full_context_hash,
        logical_model=_safe_receipt_metadata(compiled.model, fallback="invalid-model"),
        resolved_model=_safe_receipt_metadata(resolved_model, fallback="invalid-model"),
        observed_provider=_safe_receipt_metadata(observed_provider),
        stream=stream,
        status=status,
        error_code=error_code,
        http_status=http_status,
        first_text_ms=first_text_ms,
        full_response_ms=full_response_ms,
        total_ms=total_ms,
        output_chars=output_chars,
        chunk_count=chunk_count,
        provider_usage_present=usage.present,
        usage_status=usage.status,
        input_tokens=usage.input_tokens,
        cached_tokens=usage.cached_tokens,
        cache_write_tokens=usage.cache_write_tokens,
        output_tokens=usage.output_tokens,
        total_tokens=usage.total_tokens,
    )


def _record_receipt(receipt, payload, started):
    _record_transport_best_effort(
        model_name=receipt.logical_model,
        payload=payload,
        stream=receipt.stream,
        status=receipt.status,
        started=started,
        first_text_ms=receipt.first_text_ms,
        full_response_ms=receipt.full_response_ms,
        total_ms=receipt.total_ms,
        chunks=receipt.chunk_count,
        output_chars=receipt.output_chars,
        error=receipt.error_code or "none",
        receipt=receipt,
    )


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


def _prepare_bound_llmgate_dispatch(
    compiled,
    *,
    max_tokens,
    reasoning_effort,
):
    route = require_compiled_private_readiness(
        compiled,
        requested_output_tokens=max_tokens,
        requested_reasoning_effort=reasoning_effort,
    )
    if route is None:
        return None
    model_config = load_llmgate_model_strict(
        route.logical_model,
        route.resolved_model,
    )
    if type(model_config) is not dict:
        raise ContextContractError("readiness_route_unavailable")
    resolved_model = model_config.get("model")
    if type(resolved_model) is not str or resolved_model != route.resolved_model:
        raise ContextContractError("readiness_model_drift")
    try:
        base_url, endpoint_fingerprint = resolve_endpoint_identity_v1(
            model_config.get("baseUrl")
        )
    except ContextContractError as exc:
        raise ContextContractError("readiness_endpoint_drift") from exc
    if endpoint_fingerprint != route.endpoint_fingerprint:
        raise ContextContractError("readiness_endpoint_drift")
    api_key = model_config.get("apiKey")
    if (
        type(api_key) is not str
        or not api_key.strip()
        or re.fullmatch(r"\$\{[^{}]+\}", api_key.strip()) is not None
    ):
        raise ContextContractError("readiness_credentials_unavailable")
    current = require_compiled_private_readiness(
        compiled,
        requested_output_tokens=max_tokens,
        requested_reasoning_effort=reasoning_effort,
    )
    if current != route:
        raise ContextContractError("readiness_route_changed")
    return _BoundLLMGateDispatch(route, api_key, base_url, resolved_model)


def _recheck_bound_llmgate_dispatch(
    compiled,
    dispatch,
    *,
    max_tokens,
    reasoning_effort,
):
    if dispatch is None:
        return
    current = require_compiled_private_readiness(
        compiled,
        requested_output_tokens=max_tokens,
        requested_reasoning_effort=reasoning_effort,
    )
    if current != dispatch.route:
        raise ContextContractError("readiness_route_changed")


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
        _record_transport_best_effort(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="complete",
            started=started,
            full_response_ms=elapsed_ms,
            chunks=1,
            output_chars=len(content),
        )
        _best_effort_log("runtime", f"LLMGate sidecar used: {model_name}")
        return content, "ok"
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "unknown"
        body = ""
        if exc.response is not None:
            body = (exc.response.text or "").strip().replace("\n", " ")
        debug = f"http_error: {status} | {body[:180] or 'no_body'}"
        _record_transport_best_effort(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="failed",
            started=started,
            error="http_error",
        )
        return None, debug
    except Exception as exc:
        debug = f"client_exception: {type(exc).__name__}"
        _record_transport_best_effort(
            model_name=model_name,
            payload=payload,
            stream=False,
            status="failed",
            started=started,
            error="client_exception",
        )
        return None, debug


def _call_llmgate_messages_shared(
    model_name,
    messages,
    max_tokens,
    temperature,
    reasoning_effort,
    *,
    timeout_s,
    compiled=None,
    receipt_observer=None,
):
    bound_dispatch = None
    if compiled is not None:
        messages = prepare_compiled_transport_messages(compiled)
        bound_dispatch = _prepare_bound_llmgate_dispatch(
            compiled,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    started = time.perf_counter()
    response = None
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    usage = _UsageObservation()
    observed_provider = None
    resolved_model = model_name
    http_status = None

    def finish(content, debug, *, status, error_code=None):
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        if compiled is None:
            _record_transport_best_effort(
                model_name=model_name,
                payload=payload,
                stream=False,
                status=status,
                started=started,
                full_response_ms=elapsed_ms if status == "complete" else None,
                chunks=1 if content is not None else 0,
                output_chars=len(content or ""),
                error=error_code or "none",
            )
            return content, debug, None
        receipt = _make_transport_receipt(
            compiled,
            resolved_model=resolved_model,
            observed_provider=observed_provider,
            stream=False,
            status=status,
            error_code=error_code,
            http_status=http_status,
            first_text_ms=None,
            full_response_ms=elapsed_ms if status == "complete" else None,
            total_ms=elapsed_ms,
            output_chars=len(content or ""),
            chunk_count=1 if content is not None else 0,
            usage=usage,
        )
        _record_receipt(receipt, payload, started)
        _best_effort_observe(receipt_observer, receipt)
        return content, debug, receipt

    if bound_dispatch is None:
        model_config = load_llmgate_model(model_name)
        if not model_config:
            return finish(
                None,
                f"model_not_configured: {model_name}",
                status="failed",
                error_code="model_not_configured",
            )
        api_key = model_config.get("apiKey")
        base_url = (model_config.get("baseUrl") or "").rstrip("/")
        resolved_model = model_config.get("model") or model_name
    else:
        api_key = bound_dispatch.api_key
        base_url = bound_dispatch.base_url
        resolved_model = bound_dispatch.resolved_model
    payload["model"] = resolved_model
    if not api_key:
        return finish(
            None,
            "missing_api_key",
            status="failed",
            error_code="missing_api_key",
        )
    if not base_url:
        return finish(
            None,
            "missing_base_url",
            status="failed",
            error_code="missing_base_url",
        )
    effective_reasoning_effort = (
        reasoning_effort
        if bound_dispatch is None
        else bound_dispatch.route.reasoning_effort
    )
    apply_llmgate_reasoning_effort(
        payload,
        model_name,
        requested=effective_reasoning_effort,
    )
    request_timeout = (
        LLMGATE_TIMEOUT
        if timeout_s is None
        else min(LLMGATE_TIMEOUT, max(0.1, float(timeout_s)))
    )
    if compiled is not None:
        require_compiled_transport_integrity(compiled, payload["messages"])
        _recheck_bound_llmgate_dispatch(
            compiled,
            bound_dispatch,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

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
        raw_status = getattr(response, "status_code", None)
        if type(raw_status) is int and 100 <= raw_status <= 599:
            http_status = raw_status
        response.raise_for_status()
        data = response.json()
        if bound_dispatch is not None:
            if type(data) is not dict or data.get("model") is None:
                raise ContextContractError("readiness_response_model_unverified")
            if data.get("model") != bound_dispatch.resolved_model:
                raise ContextContractError("readiness_response_model_mismatch")
            resolved_model = data["model"]
        content = data["choices"][0]["message"]["content"].strip()
        usage = _normalize_provider_usage(data)
        observed_provider = _observed_provider(data)
        result = finish(content, "ok", status="complete")
        _best_effort_log("runtime", f"LLMGate chat used: {model_name}")
        return result
    except requests.HTTPError as exc:
        if exc.response is not None:
            raw_status = getattr(exc.response, "status_code", None)
            if type(raw_status) is int and 100 <= raw_status <= 599:
                http_status = raw_status
        status_label = http_status if http_status is not None else "unknown"
        debug = f"http_error: {status_label}"
        return finish(None, debug, status="failed", error_code="http_error")
    except ContextContractError:
        raise
    except Exception as exc:
        debug = f"client_exception: {type(exc).__name__}"
        return finish(None, debug, status="failed", error_code="client_exception")
    finally:
        _best_effort_close(response)


def call_llmgate_messages(
    model_name,
    messages,
    max_tokens=180,
    temperature=0.2,
    reasoning_effort=None,
    *,
    timeout_s=None,
):
    content, debug, _receipt = _call_llmgate_messages_shared(
        model_name,
        messages,
        max_tokens,
        temperature,
        reasoning_effort,
        timeout_s=timeout_s,
    )
    return content, debug


def call_llmgate_compiled(
    compiled,
    *,
    max_tokens,
    temperature,
    reasoning_effort=None,
    timeout_s=None,
    receipt_observer=None,
):
    """Send one compiler-issued context and return content, debug, receipt."""

    return _call_llmgate_messages_shared(
        compiled.model,
        None,
        max_tokens,
        temperature,
        reasoning_effort,
        timeout_s=timeout_s,
        compiled=compiled,
        receipt_observer=receipt_observer,
    )


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
        _best_effort_log("runtime", f"LLMGate vision sidecar used: {model_name}")
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


def load_llmgate_model_strict(logical_model, resolved_model):
    """Resolve one explicit configured tuple without the dashboard clone fallback."""

    logical = str(logical_model or "").strip()
    actual = str(resolved_model or "").strip()
    if not logical or not actual:
        return None
    settings = load_llmgate_settings()
    if type(settings) is not dict:
        return None
    candidates = set(_candidate_model_names(logical))
    matches = []
    for raw_model in settings.get("customModels", ()):
        if type(raw_model) is not dict:
            continue
        model = resolve_model_env_placeholders(raw_model)
        if type(model) is not dict or model.get("model") != actual:
            continue
        display_name = model.get("displayName")
        if logical not in {actual, display_name} and actual not in candidates:
            continue
        matches.append(dict(model))
    if len(matches) != 1:
        return None
    return matches[0]


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


def _stream_llmgate_messages_shared(
    model_name,
    messages,
    max_tokens,
    temperature,
    reasoning_effort,
    *,
    timeout_s,
    compiled=None,
    receipt_observer=None,
):
    bound_dispatch = None
    if compiled is not None:
        messages = prepare_compiled_transport_messages(compiled)
        bound_dispatch = _prepare_bound_llmgate_dispatch(
            compiled,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )
    started = time.perf_counter()
    response = None
    first_text_ms = None
    chunks = 0
    output_chars = 0
    status = "failed"
    error_code = None
    usage = _UsageObservation()
    observed_provider = None
    resolved_model = model_name
    http_status = None
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": True,
    }
    bound_response_model_verified = bound_dispatch is None

    try:
        if bound_dispatch is None:
            model_config = load_llmgate_model(model_name)
            if not model_config:
                error_code = "model_not_configured"
                return
            api_key = model_config.get("apiKey")
            base_url = (model_config.get("baseUrl") or "").rstrip("/")
            resolved_model = model_config.get("model") or model_name
        else:
            api_key = bound_dispatch.api_key
            base_url = bound_dispatch.base_url
            resolved_model = bound_dispatch.resolved_model
        payload["model"] = resolved_model
        if not api_key:
            error_code = "missing_api_key"
            return
        if not base_url:
            error_code = "missing_base_url"
            return
        effective_reasoning_effort = (
            reasoning_effort
            if bound_dispatch is None
            else bound_dispatch.route.reasoning_effort
        )
        apply_llmgate_reasoning_effort(
            payload,
            model_name,
            requested=effective_reasoning_effort,
        )
        if compiled is not None:
            require_compiled_transport_integrity(compiled, payload["messages"])
        request_timeout = (
            LLMGATE_TIMEOUT
            if timeout_s is None
            else min(LLMGATE_TIMEOUT, max(0.1, float(timeout_s)))
        )
        import sseclient

        if compiled is not None:
            _recheck_bound_llmgate_dispatch(
                compiled,
                bound_dispatch,
                max_tokens=max_tokens,
                reasoning_effort=reasoning_effort,
            )

        response = _http_post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=request_timeout,
            stream=True,
        )
        raw_status = getattr(response, "status_code", None)
        if type(raw_status) is int and 100 <= raw_status <= 599:
            http_status = raw_status
        response.raise_for_status()
        client = sseclient.SSEClient(response)
        invalid_event_code = None
        for event in client.events():
            data = getattr(event, "data", None)
            if type(data) is not str or not data.strip():
                if invalid_event_code is None:
                    invalid_event_code = "malformed_sse_event"
                continue
            if data.strip() == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except (json.JSONDecodeError, TypeError):
                if invalid_event_code is None:
                    invalid_event_code = "malformed_sse_event"
                continue
            if bound_dispatch is not None and type(obj) is dict and "model" in obj:
                response_model = obj.get("model")
                if response_model != bound_dispatch.resolved_model:
                    raise ContextContractError("readiness_response_model_mismatch")
                bound_response_model_verified = True
            event_usage = _normalize_provider_usage(obj)
            if event_usage.present:
                usage = event_usage
            event_provider = _observed_provider(obj)
            if event_provider is not None:
                observed_provider = event_provider
            choices = obj.get("choices") if type(obj) is dict else None
            if choices == [] and event_usage.present:
                continue
            if type(choices) is not list or not choices:
                if invalid_event_code is None:
                    invalid_event_code = "empty_choices"
                continue
            choice = choices[0]
            delta = choice.get("delta") if type(choice) is dict else None
            content = delta.get("content") if type(delta) is dict else None
            if content in {None, ""}:
                continue
            if type(content) is not str:
                if invalid_event_code is None:
                    invalid_event_code = "malformed_sse_event"
                continue
            if not bound_response_model_verified:
                raise ContextContractError("readiness_response_model_unverified")
            if first_text_ms is None:
                first_text_ms = (time.perf_counter() - started) * 1000.0
            chunks += 1
            output_chars += len(content)
            yield content
        if bound_dispatch is not None and not bound_response_model_verified:
            raise ContextContractError("readiness_response_model_unverified")
        if invalid_event_code is None:
            status = "complete"
        else:
            status = "complete_with_invalid_events"
            error_code = invalid_event_code
        _best_effort_log("runtime", f"LLMGate stream used: {model_name}")
    except GeneratorExit:
        status = "cancelled"
        error_code = "generator_closed"
        raise
    except requests.HTTPError as exc:
        status = "failed"
        error_code = "http_error"
        if exc.response is not None:
            raw_status = getattr(exc.response, "status_code", None)
            if type(raw_status) is int and 100 <= raw_status <= 599:
                http_status = raw_status
        _best_effort_log("errors", "LLMGate stream failed: http_error")
    except ContextContractError as exc:
        error_code = str(exc)
        raise
    except Exception:
        status = "failed"
        error_code = "client_exception"
        _best_effort_log("errors", "LLMGate stream failed: client_exception")
    finally:
        _best_effort_close(response)
        total_ms = (time.perf_counter() - started) * 1000.0
        if compiled is None:
            _record_transport_best_effort(
                model_name=model_name,
                payload=payload,
                stream=True,
                status=status,
                started=started,
                first_text_ms=first_text_ms,
                total_ms=total_ms,
                chunks=chunks,
                output_chars=output_chars,
                error=error_code or "none",
            )
        else:
            receipt = _make_transport_receipt(
                compiled,
                resolved_model=resolved_model,
                observed_provider=observed_provider,
                stream=True,
                status=status,
                error_code=error_code,
                http_status=http_status,
                first_text_ms=first_text_ms,
                full_response_ms=None,
                total_ms=total_ms,
                output_chars=output_chars,
                chunk_count=chunks,
                usage=usage,
            )
            _record_receipt(receipt, payload, started)
            _best_effort_observe(receipt_observer, receipt)


def stream_llmgate_messages(
    model_name,
    messages,
    max_tokens=1000,
    temperature=0.75,
    reasoning_effort=None,
):
    """Stream LLM response via SSE. Yields text chunks as they arrive."""

    yield from _stream_llmgate_messages_shared(
        model_name,
        messages,
        max_tokens,
        temperature,
        reasoning_effort,
        timeout_s=None,
    )


def stream_llmgate_compiled(
    compiled,
    *,
    max_tokens,
    temperature,
    reasoning_effort=None,
    timeout_s=None,
    receipt_observer=None,
):
    """Stream exact compiler-issued messages and observe one terminal receipt."""

    yield from _stream_llmgate_messages_shared(
        compiled.model,
        None,
        max_tokens,
        temperature,
        reasoning_effort,
        timeout_s=timeout_s,
        compiled=compiled,
        receipt_observer=receipt_observer,
    )
