"""Direct OpenAI Chat Completions transport for Nana's private owner chat.

Default OFF. When ``NANA_PRIVATE_LLM_PROVIDER=openai_direct`` the legacy private
sync/stream paths in ``brain/gpt.py`` try this transport first and fall back to
the existing LLMGate route when it is unavailable or fails before any text.
Public stage, CUM2, autonomy, sidecar and canonical compiled paths never use it.

Settings are read from the environment at call time (``config.py`` has already
loaded ``<repository>/.env`` into it); only ``OPENAI_API_KEY`` comes from ``config``:

    NANA_PRIVATE_LLM_PROVIDER            llmgate (default) | openai_direct
    NANA_OPENAI_DIRECT_MODEL             default gpt-5.6-terra
    NANA_OPENAI_DIRECT_REASONING_EFFORT  none (default) | minimal | low | ...
    NANA_OPENAI_DIRECT_BASE_URL          default https://api.openai.com/v1
    NANA_OPENAI_DIRECT_TIMEOUT_S         default 30, bounded 5..120

Current OpenAI reasoning-capable models need ``max_completion_tokens`` instead
of ``max_tokens`` and accept only the default temperature unless
``reasoning_effort`` is ``none``; the payload builder applies those rules.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time

import requests

from nana import config
from nana.runtime.logger import log_event

_HTTP_LOCAL = threading.local()
_PLACEHOLDER_KEYS = {"", "OPENAI_KEY_CUA_BAN"}
_CONNECT_TIMEOUT_S = 5.0
_REASONING_EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max"}
_DEFAULT_BASE_URL = "https://api.openai.com/v1"


def _env(name: str, default: str) -> str:
    return (os.getenv(name) or "").strip() or default


def openai_direct_private_enabled() -> bool:
    return _env("NANA_PRIVATE_LLM_PROVIDER", "llmgate").lower() == "openai_direct"


def openai_direct_model() -> str:
    return _env("NANA_OPENAI_DIRECT_MODEL", "gpt-5.6-terra")


def openai_direct_reasoning_effort() -> str:
    effort = _env("NANA_OPENAI_DIRECT_REASONING_EFFORT", "none").lower()
    return effort if effort in _REASONING_EFFORTS else "none"


def openai_direct_endpoint_host() -> str:
    from urllib.parse import urlparse

    return urlparse(_env("NANA_OPENAI_DIRECT_BASE_URL", _DEFAULT_BASE_URL)).netloc or "invalid"


def openai_direct_key_present() -> bool:
    return _api_key() is not None


def private_route_snapshot(llmgate_model: str) -> dict:
    """Read-only description of which transport serves private owner chat."""
    if not openai_direct_private_enabled():
        return {
            "provider": "llmgate",
            "model": llmgate_model,
            "reasoning_effort": None,
            "endpoint_host": "llmgate",
            "key_present": None,
            "fallback": "none",
            "transport_model": llmgate_model,
        }
    model = openai_direct_model()
    return {
        "provider": "openai_direct",
        "model": model,
        "reasoning_effort": openai_direct_reasoning_effort(),
        "endpoint_host": openai_direct_endpoint_host(),
        "key_present": openai_direct_key_present(),
        "fallback": f"llmgate:{llmgate_model}",
        "transport_model": "openai:" + model,
    }


_MODEL_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\-]{0,63}")


def set_private_route_for_session(provider: str, model: str | None = None, effort: str | None = None):
    """Switch the private route for this Core process only (no .env write).

    Returns ``(ok, message)``. Takes effect on the next private turn because
    the route settings are read from the environment at call time.
    """
    provider = str(provider or "").strip().lower()
    if provider not in {"llmgate", "openai_direct"}:
        return False, "provider must be llmgate or openai_direct"
    if provider == "llmgate":
        if model or effort:
            return False, "llmgate takes no model/effort here; it uses the configured LLMGate main model"
        os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "llmgate"
        return True, "private chat -> llmgate (this session only)"
    if model is not None and not _MODEL_NAME_RE.fullmatch(str(model)):
        return False, "invalid model name"
    if effort is not None and str(effort).strip().lower() not in _REASONING_EFFORTS:
        return False, "effort must be one of " + "|".join(sorted(_REASONING_EFFORTS))
    if not openai_direct_key_present():
        return False, "OPENAI_API_KEY missing or placeholder; staying on current route"
    os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "openai_direct"
    if model is not None:
        os.environ["NANA_OPENAI_DIRECT_MODEL"] = str(model)
    if effort is not None:
        os.environ["NANA_OPENAI_DIRECT_REASONING_EFFORT"] = str(effort).strip().lower()
    return True, (
        f"private chat -> openai_direct {openai_direct_model()} "
        f"(effort={openai_direct_reasoning_effort()}, this session only)"
    )


def private_route_command_lines(text: str, llmgate_model: str) -> list[str]:
    """Implement ``/llm-provider [llmgate|openai_direct [model] [effort]]``."""
    parts = str(text or "").split()[1:]
    lines = ["LLM Provider (private owner chat)"]
    if len(parts) > 3:
        # Validate the whole command before touching any route setting.
        lines.append("  Not changed: usage: /llm-provider [llmgate|openai_direct [model] [effort]]")
    elif parts:
        ok, message = set_private_route_for_session(
            parts[0], parts[1] if len(parts) > 1 else None, parts[2] if len(parts) > 2 else None
        )
        lines.append(("  Changed: " if ok else "  Not changed: ") + message)
    snap = private_route_snapshot(llmgate_model)
    if snap["provider"] == "openai_direct":
        lines.append(
            f"  Active: openai_direct -> {snap['model']} | effort={snap['reasoning_effort']} | "
            f"endpoint={snap['endpoint_host']} | key_present={snap['key_present']} | fallback={snap['fallback']}"
        )
    else:
        lines.append(f"  Active: llmgate -> {snap['model']}")
    lines.append("  Scope: this Core process only; <repository>/.env is unchanged and applies again after restart.")
    lines.append("  Usage: /llm-provider llmgate | /llm-provider openai_direct [model] [effort]")
    return lines


def _timeout_s() -> float:
    try:
        value = int(_env("NANA_OPENAI_DIRECT_TIMEOUT_S", "30"))
    except ValueError:
        value = 30
    return float(max(5, min(120, value)))


def _api_key() -> str | None:
    key = str(getattr(config, "OPENAI_API_KEY", "") or "").strip()
    return None if key in _PLACEHOLDER_KEYS else key


def _session() -> requests.Session:
    session = getattr(_HTTP_LOCAL, "session", None)
    if session is None:
        session = requests.Session()
        _HTTP_LOCAL.session = session
    return session


def _http_post(url, **kwargs):
    return _session().post(url, **kwargs)


def build_openai_direct_payload(messages, *, max_tokens, temperature, stream):
    effort = openai_direct_reasoning_effort()
    payload = {
        "model": openai_direct_model(),
        "messages": messages,
        "max_completion_tokens": int(max_tokens),
        "stream": bool(stream),
    }
    payload["reasoning_effort"] = effort
    if effort == "none":
        payload["temperature"] = temperature
    if stream:
        payload["stream_options"] = {"include_usage": True}
    return payload


def _endpoint() -> str:
    return _env("NANA_OPENAI_DIRECT_BASE_URL", _DEFAULT_BASE_URL).rstrip("/") + "/chat/completions"


def _timeout():
    return (_CONNECT_TIMEOUT_S, _timeout_s())


def _record(
    payload,
    *,
    stream,
    status,
    started,
    first_text_ms=None,
    chunks=0,
    output_chars=0,
    error="none",
    usage=None,
    http_status=None,
    observed_provider="openai",
):
    """Reuse the existing route telemetry so /llm-route-status shows direct turns."""
    try:
        from nana.brain.llmgate_client import _record_transport_best_effort

        _record_transport_best_effort(
            model_name="openai:" + payload["model"],
            payload={
                "model": "openai:" + payload["model"],
                "messages": payload.get("messages"),
                "max_tokens": payload.get("max_completion_tokens"),
            },
            stream=stream,
            status=status,
            started=started,
            first_text_ms=first_text_ms,
            full_response_ms=None if stream else (time.perf_counter() - started) * 1000.0,
            chunks=chunks,
            output_chars=output_chars,
            error=error,
            usage=usage,
            http_status=http_status,
            observed_provider=observed_provider,
        )
    except Exception:
        pass


def call_openai_direct_messages(messages, *, max_tokens, temperature):
    """Return ``(content, debug)``; content is None when the caller should fall back."""
    key = _api_key()
    if not key:
        return None, "openai_direct_missing_key"
    payload = build_openai_direct_payload(messages, max_tokens=max_tokens, temperature=temperature, stream=False)
    started = time.perf_counter()
    response = None
    usage = None
    http_status = None
    try:
        response = _http_post(
            _endpoint(),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
            timeout=_timeout(),
        )
        http_status = response.status_code if type(response.status_code) is int else None
        if response.status_code != 200:
            error = f"http_{response.status_code}"
            _record(payload, stream=False, status="failed", started=started, error=error,
                    http_status=http_status)
            log_event("errors", f"OpenAI direct chat failed: {payload['model']} | {error}")
            return None, error
        data = response.json()
        from nana.brain.llmgate_client import _normalize_provider_usage

        usage = _normalize_provider_usage(data)
        content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        content = content.strip()
        if not content:
            _record(payload, stream=False, status="failed", started=started, error="empty_content",
                    usage=usage, http_status=http_status)
            return None, "empty_content"
        _record(payload, stream=False, status="complete", started=started, output_chars=len(content),
                usage=usage, http_status=http_status)
        log_event("runtime", f"OpenAI direct chat used: {payload['model']}")
        return content, "ok"
    except Exception as exc:
        _record(payload, stream=False, status="failed", started=started, error=type(exc).__name__,
                usage=usage, http_status=http_status)
        log_event("errors", f"OpenAI direct chat failed: {payload['model']} | {type(exc).__name__}")
        return None, f"client_exception: {type(exc).__name__}"


class OpenAIDirectUnavailable(RuntimeError):
    """Raised before the first text chunk so the caller can fall back cleanly."""


def stream_openai_direct_messages(messages, *, max_tokens, temperature):
    """Yield text chunks. Raises OpenAIDirectUnavailable only before any text.

    After the first chunk, failures end the stream (status recorded) because
    replaying the turn on another provider would duplicate spoken text.
    """
    key = _api_key()
    if not key:
        raise OpenAIDirectUnavailable("openai_direct_missing_key")
    payload = build_openai_direct_payload(messages, max_tokens=max_tokens, temperature=temperature, stream=True)
    started = time.perf_counter()
    first_text_ms = None
    chunks = 0
    output_chars = 0
    status = "aborted"
    error = "none"
    response = None
    usage = None
    http_status = None
    from nana.brain.llmgate_client import _normalize_provider_usage
    try:
        try:
            response = _http_post(
                _endpoint(),
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json=payload,
                timeout=_timeout(),
                stream=True,
            )
        except Exception as exc:
            status, error = "failed", type(exc).__name__
            raise OpenAIDirectUnavailable(error) from None
        http_status = response.status_code if type(response.status_code) is int else None
        if response.status_code != 200:
            status, error = "failed", f"http_{response.status_code}"
            raise OpenAIDirectUnavailable(error)
        response.encoding = "utf-8"
        try:
            for line in response.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except json.JSONDecodeError:
                    continue
                event_usage = _normalize_provider_usage(obj)
                if event_usage.present:
                    usage = event_usage
                for choice in obj.get("choices") or []:
                    text = (choice.get("delta") or {}).get("content")
                    if text:
                        if first_text_ms is None:
                            first_text_ms = (time.perf_counter() - started) * 1000.0
                        chunks += 1
                        output_chars += len(text)
                        yield text
        except OpenAIDirectUnavailable:
            raise
        except Exception as exc:
            status, error = "failed", type(exc).__name__
            if chunks == 0:
                raise OpenAIDirectUnavailable(error) from None
            log_event("errors", f"OpenAI direct stream interrupted: {payload['model']} | {error}")
            return
        if chunks == 0:
            status, error = "failed", "empty_stream"
            raise OpenAIDirectUnavailable(error)
        status = "complete"
        log_event("runtime", f"OpenAI direct stream used: {payload['model']}")
    finally:
        if status != "complete" and error != "none":
            log_event("errors", f"OpenAI direct stream failed: {payload['model']} | {error}")
        _record(payload, stream=True, status=status, started=started, first_text_ms=first_text_ms,
                chunks=chunks, output_chars=output_chars, error=error, usage=usage,
                http_status=http_status, observed_provider="openai")
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
