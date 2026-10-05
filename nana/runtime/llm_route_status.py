"""LLM route status and explicit probe helpers.

Status paths are read-only and never call a model. Probe paths are explicit
operator commands used to measure a narrow model call.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import time
from typing import Callable

from nana.brain.llmgate_client import (
    call_llmgate_messages,
    llmgate_transport_snapshot,
    llmgate_transport_stats,
    load_llmgate_model,
    resolve_llmgate_reasoning_effort,
)
from nana.config import (
    LLM_CHAT_MAX_TOKENS,
    LLM_COMPACT_PRIVATE_PROMPT_ENABLED,
    LLM_PROMPT_MEMORY_RULE_LIMIT,
    LLM_PROMPT_RECENT_CHAT_LINES,
    LLM_PROMPT_RETRIEVAL_LIMIT,
    LLM_PROMPT_SHORT_TERM_LINES,
    LLM_STORY_MAX_TOKENS,
    LLMGATE_CHEAP_MODEL,
    LLMGATE_FALLBACK_MODELS,
    LLMGATE_MAIN_MODEL,
    LLMGATE_PUBLIC_FALLBACK_MODELS,
    LLMGATE_PUBLIC_MODEL,
    NANA_CHAT_PROVIDER,
    NANA_OPENAI_FALLBACK_ENABLED,
)
from nana.core.format import shorten_line
from nana.runtime.llm_private_fast_lane import (
    fast_lane_config_snapshot,
    fast_lane_snapshot,
)


PHASE = "LLM-ROUTE"
DEFAULT_PROBE_TEXT = "phong nay hoi im qua nana"
DEFAULT_BAKEOFF_MODELS = [
    "gemini-3-flash",
    "grok-4.20-0309-non-reasoning",
    "gpt-5.4-mini",
    "gemini-3.1-flash-lite",
]


def _unique(items: list[str]) -> list[str]:
    result: list[str] = []
    for item in items:
        clean = str(item or "").strip()
        if clean and clean not in result:
            result.append(clean)
    return result


def public_model_order() -> list[str]:
    primary = LLMGATE_PUBLIC_MODEL or LLMGATE_CHEAP_MODEL or LLMGATE_MAIN_MODEL
    return _unique([primary, *LLMGATE_PUBLIC_FALLBACK_MODELS, LLMGATE_CHEAP_MODEL])


def core_model_order() -> list[str]:
    return _unique([LLMGATE_MAIN_MODEL, *LLMGATE_FALLBACK_MODELS, LLMGATE_CHEAP_MODEL])


def cheap_model_order() -> list[str]:
    return _unique([LLMGATE_CHEAP_MODEL, *LLMGATE_FALLBACK_MODELS, LLMGATE_MAIN_MODEL])


def known_route_models() -> list[str]:
    fast_model = fast_lane_config_snapshot().get("model")
    return _unique(
        [*public_model_order(), *core_model_order(), *cheap_model_order(), fast_model]
    )


def _route_text(models: list[str]) -> str:
    return " -> ".join(models) if models else "none"


def _latency_pair(summary: dict) -> str:
    if not summary or not summary.get("count"):
        return "none"
    return f"{float(summary.get('p50_ms')):.0f}/{float(summary.get('p95_ms')):.0f}ms"


def _model_readiness(model_name: str) -> str:
    config = load_llmgate_model(model_name)
    if not config:
        return "missing"
    if not config.get("apiKey"):
        return "missing_api_key"
    if not config.get("baseUrl"):
        return "missing_base_url"
    payload_model = str(config.get("model") or "").strip()
    if payload_model and payload_model != model_name:
        return f"ready(alias:{payload_model})"
    return "ready"


def _private_route(llmgate_model: str) -> dict:
    try:
        from nana.brain.openai_direct_client import private_route_snapshot

        return private_route_snapshot(llmgate_model)
    except Exception:
        return {
            "provider": "llmgate",
            "model": llmgate_model,
            "reasoning_effort": None,
            "endpoint_host": "llmgate",
            "key_present": None,
            "fallback": "none",
            "transport_model": llmgate_model,
        }


def _private_route_text(route: dict) -> str:
    if route.get("provider") != "openai_direct":
        return f"llmgate -> {route.get('model')} (NANA_PRIVATE_LLM_PROVIDER=llmgate)"
    return (
        f"openai_direct -> {route.get('model')} | effort={route.get('reasoning_effort')} | "
        f"endpoint={route.get('endpoint_host')} | key_present={route.get('key_present')} | "
        f"fallback={route.get('fallback')}"
    )


def llm_route_snapshot() -> dict:
    public = public_model_order()
    core = core_model_order()
    cheap = cheap_model_order()
    models = known_route_models()
    readiness = {model: _model_readiness(model) for model in models}
    core_primary = core[0] if core else "none"
    core_effort = resolve_llmgate_reasoning_effort(core_primary)
    private_route = _private_route(core_primary)
    transport = llmgate_transport_snapshot()
    core_stream_stats = llmgate_transport_stats(
        model_name=private_route["transport_model"],
        stream=True,
        limit=20,
    )
    fast_config = fast_lane_config_snapshot()
    fast_state = fast_lane_snapshot()
    return {
        "phase": PHASE,
        "provider": NANA_CHAT_PROVIDER,
        "openai_fallback": bool(NANA_OPENAI_FALLBACK_ENABLED),
        "public_primary": public[0] if public else "none",
        "public_order": public,
        "core_primary": core_primary,
        "core_reasoning_effort": core_effort or "provider_default",
        "core_order": core,
        "private_route": private_route,
        "cheap_primary": cheap[0] if cheap else "none",
        "cheap_order": cheap,
        "readiness": readiness,
        "prompt_budget": {
            "compact_private": bool(LLM_COMPACT_PRIVATE_PROMPT_ENABLED),
            "short_term_lines": int(LLM_PROMPT_SHORT_TERM_LINES),
            "recent_chat_lines": int(LLM_PROMPT_RECENT_CHAT_LINES),
            "retrieval_limit": int(LLM_PROMPT_RETRIEVAL_LIMIT),
            "memory_rule_limit": int(LLM_PROMPT_MEMORY_RULE_LIMIT),
            "chat_max_tokens": int(LLM_CHAT_MAX_TOKENS),
            "story_max_tokens": int(LLM_STORY_MAX_TOKENS),
        },
        "transport": transport,
        "core_stream_stats": core_stream_stats,
        "private_fast": {
            "config": fast_config,
            "state": fast_state,
        },
        "read_only": True,
        "api_call": False,
    }


def llm_route_status_lines() -> list[str]:
    snap = llm_route_snapshot()
    readiness = snap.get("readiness") or {}
    budget = snap.get("prompt_budget") or {}
    transport = snap.get("transport") or {}
    core_stats = snap.get("core_stream_stats") or {}
    core_first = core_stats.get("first_text") or {}
    core_tail = core_stats.get("tail_after_first") or {}
    core_total = core_stats.get("total") or {}
    private_fast = snap.get("private_fast") or {}
    fast_config = private_fast.get("config") or {}
    fast_state = private_fast.get("state") or {}
    ready_text = ", ".join(f"{model}={state}" for model, state in readiness.items()) or "none"
    first_text = transport.get("first_text_ms")
    full_response = transport.get("full_response_ms")
    total = transport.get("total_ms")
    tail = transport.get("tail_after_first_ms")
    first_text_label = "none" if first_text is None else f"{float(first_text):.0f}ms"
    full_response_label = (
        "none" if full_response is None else f"{float(full_response):.0f}ms"
    )
    total_label = "none" if total is None else f"{float(total):.0f}ms"
    tail_label = "none" if tail is None else f"{float(tail):.0f}ms"
    return [
        "LLM Route Status",
        "  Mode: read_only=True | api_call=False | can_act=False",
        f"  Provider: {snap.get('provider')} | openai_fallback={snap.get('openai_fallback')}",
        f"  Private route: {_private_route_text(snap.get('private_route') or {})}",
        f"  Public chat: primary={snap.get('public_primary')} | order={_route_text(snap.get('public_order') or [])}",
        (
            (
                "  Core/private LLMGate fallback: "
                if (snap.get("private_route") or {}).get("provider") == "openai_direct"
                else "  Core/private: "
            )
            + f"primary={snap.get('core_primary')} | "
            f"reasoning_effort={snap.get('core_reasoning_effort')} | "
            f"order={_route_text(snap.get('core_order') or [])}"
        ),
        f"  Cheap/banter: primary={snap.get('cheap_primary')} | order={_route_text(snap.get('cheap_order') or [])}",
        (
            "  Prompt budget: "
            f"compact_private={budget.get('compact_private')} | "
            f"short={budget.get('short_term_lines')} | recent={budget.get('recent_chat_lines')} | "
            f"retrieval={budget.get('retrieval_limit')} | rules={budget.get('memory_rule_limit')} | "
            f"tokens={budget.get('chat_max_tokens')}/{budget.get('story_max_tokens')}"
        ),
        (
            "  Last transport: "
            f"model={transport.get('model')} | stream={transport.get('stream')} | "
            f"status={transport.get('status')} | pooled={transport.get('pooled')} | "
            f"prompt={transport.get('prompt_chars')}ch | max_tokens={transport.get('max_tokens')} | "
            f"first_text={first_text_label} | "
            f"full_response={full_response_label} | "
            f"tail_after_first={tail_label} | total={total_label} | "
            f"chunks={transport.get('chunks')} | "
            f"output={transport.get('output_chars')}ch"
        ),
        (
            "  Last observed usage: "
            f"present={transport.get('provider_usage_present')} | "
            f"status={transport.get('usage_status')} | "
            f"input={transport.get('input_tokens')} | "
            f"cached={transport.get('cached_tokens')} | "
            f"cache_write={transport.get('cache_write_tokens')} | "
            f"output={transport.get('output_tokens')} | "
            f"total={transport.get('total_tokens')}"
        ),
        (
            "  Private fast pilot: "
            f"enabled={fast_config.get('enabled')} | model={fast_config.get('model')} | "
            f"transport={fast_config.get('transport')} | "
            f"input_max={fast_config.get('max_input_chars')}ch | "
            f"tokens={fast_config.get('max_tokens')} | "
            f"last={fast_state.get('last_status')}:{fast_state.get('last_decision')} | "
            f"validation={fast_state.get('last_validation')} | "
            f"prompt={fast_state.get('last_prompt_chars')}ch | "
            f"accepted={fast_state.get('accepted')}/{fast_state.get('attempts')} | "
            f"fallbacks={fast_state.get('fallbacks')}"
        ),
        (
            "  Core rolling latency: "
            f"model={core_stats.get('model')} | stream=True | n={core_stats.get('window', 0)}/20 | "
            f"first_text p50/p95={_latency_pair(core_first)} | "
            f"tail p50/p95={_latency_pair(core_tail)} | "
            f"total p50/p95={_latency_pair(core_total)}"
        ),
        f"  Config readiness: {shorten_line(ready_text, 220)}",
        "  Commands: /llm-route-status | /llm-route-probe [model] [text] | /llm-route-bakeoff [text]",
        "  Safety: status never calls API; probe calls API only when explicit.",
    ]


@dataclass(frozen=True)
class ProbeArgs:
    model: str
    text: str


def parse_probe_args(raw_args: str | None) -> ProbeArgs:
    raw = str(raw_args or "").strip()
    default_model = (public_model_order() or [LLMGATE_CHEAP_MODEL or LLMGATE_MAIN_MODEL])[0]
    if not raw:
        return ProbeArgs(model=default_model, text=DEFAULT_PROBE_TEXT)

    first, sep, rest = raw.partition(" ")
    known = set(known_route_models())
    looks_like_model = bool(re.match(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$", first)) and (
        first in known
        or "-" in first
        or first.startswith(("gpt", "gemini", "grok", "claude", "kimi", "glm", "llama", "minimax", "qwen"))
    )
    if looks_like_model:
        return ProbeArgs(model=first, text=(rest.strip() if sep else DEFAULT_PROBE_TEXT))
    return ProbeArgs(model=default_model, text=raw)


ProbeCaller = Callable[..., tuple[str | None, str]]


def _probe_messages(text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are Nana's narrow model route probe. Reply in Vietnamese as Nana, "
                "one or two natural sentences. Do not mention diagnostics."
            ),
        },
        {"role": "user", "content": text},
    ]


def llm_route_probe_lines(
    raw_args: str | None = None,
    *,
    caller: ProbeCaller = call_llmgate_messages,
) -> list[str]:
    args = parse_probe_args(raw_args)
    messages = _probe_messages(args.text)
    started = time.perf_counter()
    content, debug = caller(args.model, messages, max_tokens=90, temperature=0.55)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    ok = bool(content)
    effort = resolve_llmgate_reasoning_effort(args.model) or "provider_default"
    return [
        "LLM Route Probe",
        f"  OK: {ok} | model={args.model} | reasoning_effort={effort} | ms={elapsed_ms:.0f} | reason={debug}",
        f"  Prompt: {shorten_line(args.text, 140)}",
        f"  Reply: {shorten_line(content or 'none', 360)}",
        "  Safety: explicit API probe only | no TTS/VTS/OBS/Discord/game input",
    ]


@dataclass(frozen=True)
class BakeoffArgs:
    models: list[str]
    text: str


@dataclass(frozen=True)
class BakeoffResult:
    model: str
    ok: bool
    elapsed_ms: float
    reason: str
    reply: str
    warnings: list[str]


def parse_bakeoff_args(raw_args: str | None) -> BakeoffArgs:
    raw = str(raw_args or "").strip()
    models = list(DEFAULT_BAKEOFF_MODELS)
    text = DEFAULT_PROBE_TEXT
    if not raw:
        return BakeoffArgs(models=_unique(models), text=text)

    if raw.startswith("--models="):
        first, _, rest = raw.partition(" ")
        model_text = first.replace("--models=", "", 1).strip()
        parsed = [item.strip() for item in model_text.split(",") if item.strip()]
        if parsed:
            models = parsed
        text = rest.strip() or DEFAULT_PROBE_TEXT
    else:
        text = raw
    return BakeoffArgs(models=_unique(models), text=text)


def _quality_warnings(reply: str | None) -> list[str]:
    text = (reply or "").strip()
    lowered = text.lower()
    warnings: list[str] = []
    if not text:
        return ["empty_reply"]
    has_private_nana_voice = "nana" in lowered or (
        re.search(r"\bba\b", lowered) is not None
        and re.search(r"\bcon\b", lowered) is not None
    )
    if not has_private_nana_voice:
        warnings.append("missing_nana_voice")
    foreign_identity_markers = (
        "i'm kiro",
        "i am kiro",
        "ai development environment",
        "created as kiro",
        "designed to help you with coding",
    )
    if any(marker in lowered for marker in foreign_identity_markers):
        warnings.append("foreign_identity")
    refusal_markers = (
        "i can't discuss that",
        "i cannot discuss that",
        "can't roleplay as",
        "cannot roleplay as",
        "stick with who i actually am",
    )
    if any(marker in lowered for marker in refusal_markers):
        warnings.append("identity_refusal")
    if re.search(r"\bmình\b", lowered):
        warnings.append("uses_minh")
    if any(mark in text for mark in ("😊", "😀", "🙂", "😂", "😅", "✨", "❤️")):
        warnings.append("emoji")
    service_patterns = [
        r"c[uứ]\s+n[oó]i\s+ti[eế]p",
        r"m[ìi]nh\s+đang\s+theo\s+d[oõ]i",
        r"h[oỗ]\s+tr[oợ]",
        r"b[aạ]n\s+th[eế]\s+n[aà]o",
        r"k[eể]\s+.*nghe",
        r"tr[oò]\s+chuy[eệ]n\s+c[uù]ng\s+b[aạ]n",
    ]
    if any(re.search(pattern, lowered) for pattern in service_patterns):
        warnings.append("service_tone")
    if len(text) < 45:
        warnings.append("too_short")
    if len(text) > 260:
        warnings.append("too_long")
    return _unique(warnings)


def _suggest_bakeoff_result(results: list[BakeoffResult]) -> tuple[str, str]:
    ok_results = [result for result in results if result.ok]
    if not ok_results:
        return "none", "all_failed"
    identity_failures = {"foreign_identity", "identity_refusal"}
    eligible = [
        result
        for result in ok_results
        if not identity_failures.intersection(result.warnings)
    ]
    if not eligible:
        return "none", "all_identity_failed"
    clean = [result for result in eligible if not result.warnings]
    if clean:
        best = min(clean, key=lambda item: item.elapsed_ms)
        return best.model, "fastest_clean"
    light = [result for result in eligible if len(result.warnings) <= 1]
    if light:
        best = min(light, key=lambda item: item.elapsed_ms)
        return best.model, "fastest_with_minor_warning"
    best = min(eligible, key=lambda item: (len(item.warnings), item.elapsed_ms))
    return best.model, "manual_review_quality_warnings"


def llm_route_bakeoff_lines(
    raw_args: str | None = None,
    *,
    caller: ProbeCaller = call_llmgate_messages,
) -> list[str]:
    args = parse_bakeoff_args(raw_args)
    results: list[BakeoffResult] = []
    for model in args.models:
        started = time.perf_counter()
        content, debug = caller(model, _probe_messages(args.text), max_tokens=90, temperature=0.55)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        reply = content or ""
        results.append(
            BakeoffResult(
                model=model,
                ok=bool(content),
                elapsed_ms=elapsed_ms,
                reason=debug,
                reply=reply,
                warnings=_quality_warnings(reply),
            )
        )

    ranked = sorted(results, key=lambda item: (not item.ok, item.elapsed_ms))
    suggested_model, suggested_reason = _suggest_bakeoff_result(results)
    lines = [
        "LLM Route Bakeoff",
        f"  Mode: explicit_api_calls=True | models={len(args.models)} | can_act=False",
        f"  Prompt: {shorten_line(args.text, 140)}",
        f"  Suggested: {suggested_model} | reason={suggested_reason}",
        "  Results:",
    ]
    for idx, result in enumerate(ranked, start=1):
        warning_text = ",".join(result.warnings) if result.warnings else "none"
        lines.append(
            f"    {idx}. {result.model} | ok={result.ok} | ms={result.elapsed_ms:.0f} | "
            f"reason={result.reason} | warnings={warning_text}"
        )
        lines.append(f"       Reply: {shorten_line(result.reply or 'none', 260)}")
    lines.append("  Safety: explicit multi-model API probe only | no TTS/VTS/OBS/Discord/game input")
    return lines
