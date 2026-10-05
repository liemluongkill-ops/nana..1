"""Offline Task 13 transport receipts and frozen-wire regressions."""

from __future__ import annotations

import copy
import importlib
from pathlib import Path
import sys
import types

import pytest
import requests

from test_context_pipeline_parity import fixture as context_fixture


@pytest.fixture(autouse=True)
def _restore_module_fixtures():
    nana_before = {
        name: module
        for name, module in sys.modules.items()
        if name == "nana" or name.startswith("nana.")
    }
    sse_before = sys.modules.get("sseclient")
    yield
    for name in tuple(sys.modules):
        if (name == "nana" or name.startswith("nana.")) and name not in nana_before:
            sys.modules.pop(name, None)
    sys.modules.update(nana_before)
    if sse_before is None:
        sys.modules.pop("sseclient", None)
    else:
        sys.modules["sseclient"] = sse_before


def _compiled_context():
    gpt, _, _, _, runtime, _, _, request, sections = context_fixture()
    plan = runtime.build_turn_plan(
        runtime.GptTurnInput(
            request=request,
            sections=sections,
            evidence=None,
            model="fake",
            max_output_tokens=200,
            budget_revision=runtime.PRIVATE_POLICY_REVISION,
        )
    )
    return gpt, plan


def _real_client(monkeypatch):
    config = sys.modules["nana.config"]
    monkeypatch.setattr(config, "LLMGATE_MAIN_REASONING_EFFORT", "none", raising=False)
    monkeypatch.setattr(config, "LLMGATE_SETTINGS_PATH", "unused-settings.json", raising=False)
    monkeypatch.setattr(config, "LLMGATE_TIMEOUT", 30.0, raising=False)
    sys.modules.pop("nana.brain.llmgate_client", None)
    client = importlib.import_module("nana.brain.llmgate_client")
    monkeypatch.setattr(
        client,
        "load_llmgate_model",
        lambda model_name: {
            "apiKey": "fixture-key",
            "baseUrl": "https://llmgate.invalid/v1",
            "model": f"resolved-{model_name}",
        },
    )
    monkeypatch.setattr(client, "log_event", lambda *_args, **_kwargs: None)
    return client


class _Response:
    def __init__(self, data=None, *, status_code=200, text=""):
        self._data = data
        self.status_code = status_code
        self.text = text
        self.closed = False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError("provider body must stay private", response=self)

    def json(self):
        return self._data

    def close(self):
        self.closed = True


def _install_sse(events):
    class FakeSSEClient:
        def __init__(self, _response):
            pass

        def events(self):
            for data in events:
                yield types.SimpleNamespace(data=data)

    sys.modules["sseclient"] = types.SimpleNamespace(SSEClient=FakeSSEClient)


def _success_data(*, usage_marker=False, usage=None):
    data = {
        "choices": [{"message": {"content": "Nana transport reply."}}],
        "provider": "fixture-provider",
    }
    if usage_marker:
        data["usage"] = usage
    return data


def test_modified_compiled_context_is_rejected_before_loader_or_http(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    object.__setattr__(plan.compiled.messages[1], "content", "tampered after compile")
    calls = {"loader": 0, "http": 0}

    def loader(_model):
        calls["loader"] += 1
        raise AssertionError("credential loader reached")

    def post(*_args, **_kwargs):
        calls["http"] += 1
        raise AssertionError("HTTP reached")

    monkeypatch.setattr(client, "load_llmgate_model", loader)
    monkeypatch.setattr(client, "_http_post", post)
    with pytest.raises(Exception, match="invalid_compiler_attestation"):
        client.call_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
        )
    assert calls == {"loader": 0, "http": 0}


def test_modified_detached_wire_is_rejected_before_http(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    http_calls = []

    def tamper(payload, _model, requested=None):
        payload["messages"][1]["content"] = "wire-only mutation"

    monkeypatch.setattr(client, "apply_llmgate_reasoning_effort", tamper)
    monkeypatch.setattr(client, "_http_post", lambda *_a, **_k: http_calls.append(1))
    with pytest.raises(Exception, match="compiled_transport_hash_mismatch"):
        client.call_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
        )
    assert http_calls == []


def test_compiled_sync_and_stream_send_same_ordered_messages(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    payloads = []

    def post(_url, **kwargs):
        payloads.append(copy.deepcopy(kwargs["json"]))
        if kwargs.get("stream"):
            return _Response()
        return _Response(_success_data())

    monkeypatch.setattr(client, "_http_post", post)
    _install_sse(
        [
            '{"choices":[{"delta":{"content":"Nana "}}]}',
            '{"choices":[{"delta":{"content":"stream."}}]}',
            "[DONE]",
        ]
    )
    content, debug, receipt = client.call_llmgate_compiled(
        plan.compiled,
        max_tokens=200,
        temperature=0.75,
    )
    observed = []
    chunks = list(
        client.stream_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
            receipt_observer=observed.append,
        )
    )
    expected = [
        {"role": message.role, "content": message.content}
        for message in plan.compiled.messages
    ]
    assert content == "Nana transport reply." and debug == "ok"
    assert chunks == ["Nana ", "stream."]
    assert [payload["messages"] for payload in payloads] == [expected, expected]
    assert receipt.full_context_hash == plan.compiled.full_context_hash
    assert observed[0].full_context_hash == plan.compiled.full_context_hash


@pytest.mark.parametrize(
    ("usage_marker", "usage", "present", "status", "values"),
    [
        (False, None, False, "missing", (None, None, None, None, None)),
        (
            True,
            {
                "input_tokens": 0,
                "input_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "output_tokens": 0,
                "total_tokens": 0,
            },
            True,
            "valid",
            (0, 0, 0, 0, 0),
        ),
        (
            True,
            {"prompt_tokens": "RAW-USAGE-MUST-NOT-LEAK"},
            True,
            "invalid",
            (None, None, None, None, None),
        ),
        (True, {"provider_specific": 7}, True, "unsupported", (None, None, None, None, None)),
    ],
)
def test_missing_zero_and_invalid_usage_are_distinct_and_safe(
    monkeypatch, usage_marker, usage, present, status, values
):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_a, **_k: _Response(
            _success_data(usage_marker=usage_marker, usage=usage)
        ),
    )
    _, _, receipt = client.call_llmgate_compiled(
        plan.compiled,
        max_tokens=200,
        temperature=0.75,
    )
    actual = (
        receipt.input_tokens,
        receipt.cached_tokens,
        receipt.cache_write_tokens,
        receipt.output_tokens,
        receipt.total_tokens,
    )
    assert receipt.provider_usage_present is present
    assert receipt.usage_status == status
    assert actual == values
    assert "RAW-USAGE-MUST-NOT-LEAK" not in repr(receipt)


def test_sync_aliases_and_full_response_latency_are_truthful(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    data = _success_data(
        usage_marker=True,
        usage={
            "prompt_tokens": 11,
            "prompt_tokens_details": {
                "cached_tokens": 3,
                "cache_write_tokens": 2,
            },
            "completion_tokens": 5,
            "total_tokens": 16,
        },
    )
    monkeypatch.setattr(client, "_http_post", lambda *_a, **_k: _Response(data))
    _, _, receipt = client.call_llmgate_compiled(
        plan.compiled,
        max_tokens=200,
        temperature=0.75,
    )
    assert (
        receipt.input_tokens,
        receipt.cached_tokens,
        receipt.cache_write_tokens,
        receipt.output_tokens,
        receipt.total_tokens,
    ) == (11, 3, 2, 5, 16)
    assert receipt.first_text_ms is None
    assert receipt.full_response_ms is not None
    assert receipt.total_ms == receipt.full_response_ms
    assert client.llmgate_transport_snapshot()["first_text_ms"] is None


def test_usage_only_terminal_sse_updates_receipt_without_text(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    response = _Response()
    monkeypatch.setattr(client, "_http_post", lambda *_a, **_k: response)
    _install_sse(
        [
            '{"choices":[{"delta":{"content":"visible"}}]}',
            '{"choices":[],"usage":{"input_tokens":9,"input_tokens_details":{"cached_tokens":4,"cache_write_tokens":1},"output_tokens":2,"total_tokens":11}}',
            "[DONE]",
        ]
    )
    receipts = []
    chunks = list(
        client.stream_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
            receipt_observer=receipts.append,
        )
    )
    assert chunks == ["visible"]
    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt.status == "complete"
    assert (receipt.input_tokens, receipt.cached_tokens) == (9, 4)
    assert receipt.chunk_count == 1 and receipt.output_chars == len("visible")
    assert response.closed is True


def test_stream_close_emits_cancelled_receipt_and_closes_response(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    response = _Response()
    monkeypatch.setattr(client, "_http_post", lambda *_a, **_k: response)
    _install_sse(
        [
            '{"choices":[{"delta":{"content":"first"}}]}',
            '{"choices":[{"delta":{"content":"second"}}]}',
        ]
    )
    receipts = []
    stream = client.stream_llmgate_compiled(
        plan.compiled,
        max_tokens=200,
        temperature=0.75,
        receipt_observer=receipts.append,
    )
    assert next(stream) == "first"
    stream.close()
    assert response.closed is True
    assert len(receipts) == 1
    assert receipts[0].status == "cancelled"
    assert receipts[0].chunk_count == 1


def test_http_failure_receipt_is_bounded_and_closes_response(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    response = _Response(status_code=503, text="RAW-PROVIDER-BODY")
    monkeypatch.setattr(client, "_http_post", lambda *_a, **_k: response)
    _install_sse([])
    receipts = []
    assert list(
        client.stream_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
            receipt_observer=receipts.append,
        )
    ) == []
    receipt = receipts[0]
    assert receipt.status == "failed"
    assert receipt.error_code == "http_error"
    assert receipt.http_status == 503
    assert "RAW-PROVIDER-BODY" not in repr(receipt)
    assert response.closed is True


def test_malformed_and_empty_choice_events_have_safe_terminal_status(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    monkeypatch.setattr(client, "_http_post", lambda *_a, **_k: _Response())
    _install_sse(
        [
            "{RAW-SSE-EVENT",
            '{"choices":[]}',
            '{"choices":[{"delta":{"content":"kept"}}]}',
            "[DONE]",
        ]
    )
    receipts = []
    assert list(
        client.stream_llmgate_compiled(
            plan.compiled,
            max_tokens=200,
            temperature=0.75,
            receipt_observer=receipts.append,
        )
    ) == ["kept"]
    assert receipts[0].status == "complete_with_invalid_events"
    assert receipts[0].error_code == "malformed_sse_event"
    assert "RAW-SSE-EVENT" not in repr(receipts[0])


def test_logger_and_observer_failures_preserve_successful_output(monkeypatch):
    _, plan = _compiled_context()
    client = _real_client(monkeypatch)
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_a, **_k: _Response(_success_data()),
    )
    monkeypatch.setattr(
        client,
        "log_event",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("logger unavailable")),
    )

    def observer(_receipt):
        raise RuntimeError("observer unavailable")

    content, debug, receipt = client.call_llmgate_compiled(
        plan.compiled,
        max_tokens=200,
        temperature=0.75,
        receipt_observer=observer,
    )
    assert content == "Nana transport reply."
    assert debug == "ok" and receipt.status == "complete"


def test_legacy_wrappers_keep_tuple_and_text_iterator_contracts(monkeypatch):
    _compiled_context()
    client = _real_client(monkeypatch)
    responses = []

    def post(_url, **kwargs):
        response = _Response(_success_data()) if not kwargs.get("stream") else _Response()
        responses.append(response)
        return response

    monkeypatch.setattr(client, "_http_post", post)
    _install_sse(['{"choices":[{"delta":{"content":"legacy-stream"}}]}', "[DONE]"])
    messages = [{"role": "user", "content": "legacy"}]
    sync_result = client.call_llmgate_messages("fake", messages)
    stream_result = list(client.stream_llmgate_messages("fake", messages))
    assert sync_result == ("Nana transport reply.", "ok")
    assert stream_result == ["legacy-stream"]
    assert all(response.closed for response in responses)


def test_legacy_http_failure_is_safe_through_gpt_fallback_logger(monkeypatch):
    gpt, _plan = _compiled_context()
    client = _real_client(monkeypatch)
    sentinel = "RAW-PROVIDER-BODY-SENTINEL-7419"
    http_models = []
    observed_tuples = []
    logs = []

    def post(_url, **kwargs):
        http_models.append(kwargs["json"]["model"])
        if len(http_models) == 1:
            return _Response(status_code=503, text=sentinel)
        return _Response(_success_data())

    def legacy_call(*args, **kwargs):
        result = client.call_llmgate_messages(*args, **kwargs)
        observed_tuples.append(result)
        return result

    monkeypatch.setattr(client, "_http_post", post)
    monkeypatch.setattr(gpt, "call_llmgate_messages", legacy_call)
    monkeypatch.setattr(gpt, "log_event", lambda channel, message: logs.append((channel, message)))
    response = gpt.create_llmgate_completion_with_fallback(
        preferred_fallback_models=["first-model", "second-model"],
        messages=[{"role": "user", "content": "fixture request"}],
        max_tokens=90,
        temperature=0.2,
    )

    assert response.choices[0].message.content == "Nana transport reply."
    assert http_models == ["resolved-first-model", "resolved-second-model"]
    assert observed_tuples == [
        (None, "http_error: 503"),
        ("Nana transport reply.", "ok"),
    ]
    rendered_logs = repr(logs)
    assert sentinel not in rendered_logs
    assert "http_error: 503" in rendered_logs


def test_legacy_http_failure_is_safe_in_probe_and_bakeoff_status(monkeypatch):
    _compiled_context()
    client = _real_client(monkeypatch)
    sentinel = "RAW-ROUTE-STATUS-SENTINEL-8527"
    monkeypatch.setattr(
        client,
        "_http_post",
        lambda *_args, **_kwargs: _Response(status_code=429, text=sentinel),
    )
    fast = sys.modules["nana.runtime.llm_private_fast_lane"]
    monkeypatch.setattr(
        fast,
        "fast_lane_config_snapshot",
        lambda: {"model": "fixture-fast"},
        raising=False,
    )
    monkeypatch.setattr(fast, "fast_lane_snapshot", lambda: {}, raising=False)
    nana_root = Path(sys.modules["nana"].__path__[0])
    core = types.ModuleType("nana.core")
    core.__path__ = [str(nana_root / "core")]
    sys.modules["nana.core"] = core
    sys.modules.pop("nana.runtime.llm_route_status", None)
    route_status = importlib.import_module("nana.runtime.llm_route_status")

    direct = client.call_llmgate_messages(
        "fixture-model",
        [{"role": "user", "content": "fixture request"}],
    )
    probe = "\n".join(
        route_status.llm_route_probe_lines(
            "fixture-model fixture request",
            caller=client.call_llmgate_messages,
        )
    )
    bakeoff = "\n".join(
        route_status.llm_route_bakeoff_lines(
            "--models=fixture-a,fixture-b fixture request",
            caller=client.call_llmgate_messages,
        )
    )

    assert direct == (None, "http_error: 429")
    assert sentinel not in probe
    assert sentinel not in bakeoff
    assert "reason=http_error: 429" in probe
    assert bakeoff.count("reason=http_error: 429") == 2


def test_canonical_gpt_uses_compiled_transport_without_legacy_fallback(monkeypatch):
    gpt, plan = _compiled_context()
    llmgate = sys.modules["nana.brain.llmgate_client"]
    calls = []

    def sync_compiled(compiled, **_options):
        calls.append(("sync", compiled))
        return "canonical-sync", "ok", object()

    def stream_compiled(compiled, **_options):
        calls.append(("stream", compiled))
        yield "canonical-stream"

    llmgate.call_llmgate_compiled = sync_compiled
    llmgate.stream_llmgate_compiled = stream_compiled
    monkeypatch.setattr(gpt, "call_llmgate_compiled", sync_compiled, raising=False)
    monkeypatch.setattr(
        gpt,
        "create_chat_completion_with_fallback",
        lambda **_k: (_ for _ in ()).throw(AssertionError("legacy fallback reached")),
    )
    llmgate.stream_llmgate_messages = lambda *_a, **_k: (_ for _ in ()).throw(
        AssertionError("legacy stream reached")
    )
    boundary = types.SimpleNamespace(
        public=False,
        livestream=False,
        interaction_scope="private_owner",
    )
    monkeypatch.setattr(
        gpt,
        "_lane_first_inputs",
        lambda **_k: (
            boundary,
            {"canonical": True},
            {},
            {"affection": 0.5, "annoyance": 0.0, "playfulness": 0.5},
        ),
    )
    monkeypatch.setattr(gpt, "_is_canonical_private_context", lambda *_a: True)
    monkeypatch.setattr(gpt, "_public_grounding_decision_for_turn", lambda *_a, **_k: None)
    monkeypatch.setattr(gpt, "_private_canonical_plan", lambda **_k: plan)
    monkeypatch.setattr(gpt, "_canonical_grounded_reply", lambda _p, reply, _t: reply)
    monkeypatch.setattr(gpt, "_core_self_repair_reply", lambda reply, **_k: reply)
    monkeypatch.setattr(gpt, "_maybe_public_reply", lambda reply, **_k: reply)
    monkeypatch.setattr(
        gpt,
        "classify_private_fast_lane",
        lambda *_a, **_k: types.SimpleNamespace(eligible=False),
    )
    monkeypatch.setattr(gpt, "record_fast_lane_decision", lambda *_a, **_k: None)

    assert gpt.ask_gpt("ordinary synthetic question") == "canonical-sync"

    async def consume():
        return "".join(
            [part async for part in gpt.ask_gpt_stream("ordinary synthetic question")]
        )

    coroutine = consume()
    try:
        coroutine.send(None)
    except StopIteration as completed:
        streamed = completed.value
    else:
        coroutine.close()
        raise AssertionError("offline canonical stream suspended")
    assert streamed == "canonical-stream"
    assert calls == [("sync", plan.compiled), ("stream", plan.compiled)]
