"""Offline smoke for the default-OFF direct OpenAI private chat route.

No network: every HTTP call is a fake. gpt.py is imported behind the same
isolated owners as smoke_llmgate_model_routing.py, so no production memory,
history, context or provider is touched.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import types
from pathlib import Path

NANA = Path(__file__).resolve().parents[2]
ROOT = NANA.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_routing_isolation():
    spec = importlib.util.spec_from_file_location(
        "openai_direct_routing_isolation", NANA / "tests" / "smoke" / "smoke_llmgate_model_routing.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module._install_isolated_dependencies()


class _FakeResponse:
    def __init__(self, status_code=200, body=None, lines=None, fail_after=None):
        self.status_code = status_code
        self._body = body or {}
        self._lines = lines or []
        self._fail_after = fail_after
        self.encoding = None
        self.closed = False

    def json(self):
        return self._body

    def iter_lines(self, decode_unicode=False):
        for index, line in enumerate(self._lines):
            if self._fail_after is not None and index == self._fail_after:
                raise ConnectionError("fixture stream cut")
            yield line

    def close(self):
        self.closed = True


def _sse(*texts, usage=True):
    lines = [
        "data: " + json.dumps({"choices": [{"delta": {"content": text}}]}, ensure_ascii=False)
        for text in texts
    ]
    if usage:
        lines.append("data: " + json.dumps({"choices": [], "usage": {"prompt_tokens": 10, "completion_tokens": 2}}))
    lines.append("data: [DONE]")
    return lines


class _Recorder:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def __call__(self, url, **kwargs):
        self.calls.append({"url": url, **kwargs})
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _boundary(scope="private_owner", public=False):
    return types.SimpleNamespace(interaction_scope=scope, public=public)


def _fake_llmgate_stream(log):
    def stream(*, model_name, messages, max_tokens, temperature):
        log.append({"model_name": model_name, "max_tokens": max_tokens, "temperature": temperature})
        yield "gate-a"
        yield "gate-b"

    return stream


MESSAGES = [{"role": "system", "content": "fixture"}, {"role": "user", "content": "Nana ơi"}]


def run_all() -> int:
    _load_routing_isolation()
    from nana import config
    import nana.brain.gpt as gpt
    import nana.brain.openai_direct_client as direct

    env_names = (
        "NANA_PRIVATE_LLM_PROVIDER", "NANA_OPENAI_DIRECT_MODEL", "NANA_OPENAI_DIRECT_REASONING_EFFORT",
        "NANA_OPENAI_DIRECT_BASE_URL", "NANA_OPENAI_DIRECT_TIMEOUT_S",
    )
    saved_env = {name: os.environ.get(name) for name in env_names}
    original_post = direct._http_post
    results = []

    def check(name, fn):
        for env_name in env_names:
            os.environ.pop(env_name, None)
        os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "openai_direct"
        os.environ["NANA_OPENAI_DIRECT_BASE_URL"] = "https://openai.invalid/v1"
        config.OPENAI_API_KEY = "fixture-openai-key"
        try:
            fn()
            results.append((name, None))
            print(f"PASS {name}")
        except Exception as exc:
            results.append((name, exc))
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
        finally:
            direct._http_post = original_post

    def payload_rules():
        p = direct.build_openai_direct_payload(MESSAGES, max_tokens=420, temperature=0.75, stream=True)
        assert p["max_completion_tokens"] == 420 and "max_tokens" not in p, p
        assert p["temperature"] == 0.75 and p["reasoning_effort"] == "none", p
        assert p["stream_options"] == {"include_usage": True} and p["model"] == "gpt-5.6-terra", p
        os.environ["NANA_OPENAI_DIRECT_REASONING_EFFORT"] = "low"
        p = direct.build_openai_direct_payload(MESSAGES, max_tokens=80, temperature=0.75, stream=False)
        assert "temperature" not in p and p["reasoning_effort"] == "low", p
        assert "stream_options" not in p, p

    def sync_ok():
        rec = _Recorder(_FakeResponse(body={
            "model": "gpt-5.6-terra",
            "choices": [{"message": {"content": " Dạ Ba "}}],
            "usage": {
                "prompt_tokens": 41,
                "prompt_tokens_details": {"cached_tokens": 39, "cache_write_tokens": 0},
                "completion_tokens": 3,
                "total_tokens": 44,
            },
        }))
        direct._http_post = rec
        content, debug = direct.call_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75)
        assert (content, debug) == ("Dạ Ba", "ok"), (content, debug)
        call = rec.calls[0]
        assert call["url"] == "https://openai.invalid/v1/chat/completions", call["url"]
        assert call["headers"]["Authorization"] == "Bearer fixture-openai-key"
        assert call["json"]["max_completion_tokens"] == 80 and call["json"]["stream"] is False
        from nana.brain import llmgate_client
        snapshot = llmgate_client.llmgate_transport_snapshot()
        assert snapshot["model"] == "openai:gpt-5.6-terra", snapshot
        assert snapshot["http_status"] == 200, snapshot
        assert snapshot["provider_usage_present"] is True, snapshot
        assert snapshot["usage_status"] == "valid", snapshot
        assert (snapshot["input_tokens"], snapshot["cached_tokens"], snapshot["cache_write_tokens"],
                snapshot["output_tokens"], snapshot["total_tokens"]) == (41, 39, 0, 3, 44), snapshot

    def sync_http_error_falls_back():
        direct._http_post = _Recorder(_FakeResponse(status_code=400))
        assert direct.call_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75) == (None, "http_400")

    def placeholder_key_never_calls_network():
        config.OPENAI_API_KEY = "OPENAI_KEY_CUA_BAN"
        rec = _Recorder(AssertionError("network must not be used"))
        direct._http_post = rec
        assert direct.call_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75) == (None, "openai_direct_missing_key")
        try:
            list(direct.stream_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75))
            raise AssertionError("expected OpenAIDirectUnavailable")
        except direct.OpenAIDirectUnavailable:
            pass
        assert rec.calls == []

    def stream_utf8_chunks():
        resp = _FakeResponse(lines=_sse("Con ", "đây Ba"))
        direct._http_post = _Recorder(resp)
        chunks = list(direct.stream_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75))
        assert chunks == ["Con ", "đây Ba"], chunks
        assert resp.encoding == "utf-8" and resp.closed

    def stream_usage_telemetry():
        resp = _FakeResponse(lines=[
            "data: " + json.dumps({"model": "gpt-5.6-terra", "choices": [{"delta": {"content": "Ba"}}]}),
            "data: " + json.dumps({"choices": [], "usage": {
                "prompt_tokens": 41,
                "prompt_tokens_details": {"cached_tokens": 39, "cache_write_tokens": 0},
                "completion_tokens": 1,
                "total_tokens": 42,
            }}),
            "data: [DONE]",
        ])
        direct._http_post = _Recorder(resp)
        assert list(direct.stream_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75)) == ["Ba"]
        from nana.brain import llmgate_client
        snapshot = llmgate_client.llmgate_transport_snapshot()
        assert snapshot["model"] == "openai:gpt-5.6-terra", snapshot
        assert snapshot["http_status"] == 200, snapshot
        assert snapshot["provider_usage_present"] is True, snapshot
        assert snapshot["usage_status"] == "valid", snapshot
        assert (snapshot["input_tokens"], snapshot["cached_tokens"], snapshot["cache_write_tokens"],
                snapshot["output_tokens"], snapshot["total_tokens"]) == (41, 39, 0, 1, 42), snapshot

    def stream_error_before_text_raises():
        direct._http_post = _Recorder(_FakeResponse(status_code=503))
        try:
            list(direct.stream_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75))
            raise AssertionError("expected OpenAIDirectUnavailable")
        except direct.OpenAIDirectUnavailable as exc:
            assert str(exc) == "http_503"

    def stream_cut_after_text_ends_quietly():
        direct._http_post = _Recorder(_FakeResponse(lines=_sse("một", "hai"), fail_after=1))
        assert list(direct.stream_openai_direct_messages(MESSAGES, max_tokens=80, temperature=0.75)) == ["một"]

    def boundary_scope_gate():
        assert gpt._openai_direct_for_boundary(_boundary()) is True
        assert gpt._openai_direct_for_boundary(_boundary(public=True)) is False
        assert gpt._openai_direct_for_boundary(_boundary(scope="public_stage")) is False
        assert gpt._openai_direct_for_boundary(_boundary(scope="bridge_operator")) is False
        os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "llmgate"
        assert gpt._openai_direct_for_boundary(_boundary()) is False

    def disabled_returns_llmgate_untouched():
        os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "llmgate"
        direct._http_post = _Recorder(AssertionError("network must not be used"))
        log = []
        chunks = list(gpt._private_chat_stream(
            _fake_llmgate_stream(log), boundary=_boundary(), model_name="gemini-3-flash",
            messages=MESSAGES, max_tokens=420, temperature=0.75,
        ))
        assert chunks == ["gate-a", "gate-b"] and log == [
            {"model_name": "gemini-3-flash", "max_tokens": 420, "temperature": 0.75}
        ], (chunks, log)

    def enabled_uses_direct_only():
        direct._http_post = _Recorder(_FakeResponse(lines=_sse("thẳng ", "OpenAI")))
        log = []
        chunks = list(gpt._private_chat_stream(
            _fake_llmgate_stream(log), boundary=_boundary(), model_name="gemini-3-flash",
            messages=MESSAGES, max_tokens=420, temperature=0.75,
        ))
        assert chunks == ["thẳng ", "OpenAI"] and log == [], (chunks, log)

    def enabled_failure_before_text_falls_back_to_llmgate():
        direct._http_post = _Recorder(ConnectionError("fixture offline"))
        log = []
        chunks = list(gpt._private_chat_stream(
            _fake_llmgate_stream(log), boundary=_boundary(), model_name="gemini-3-flash",
            messages=MESSAGES, max_tokens=420, temperature=0.75,
        ))
        assert chunks == ["gate-a", "gate-b"] and len(log) == 1, (chunks, log)

    def public_boundary_never_uses_direct():
        direct._http_post = _Recorder(AssertionError("network must not be used"))
        log = []
        chunks = list(gpt._private_chat_stream(
            _fake_llmgate_stream(log), boundary=_boundary(scope="public_stage", public=True),
            model_name="gemini-3-flash", messages=MESSAGES, max_tokens=420, temperature=0.75,
        ))
        assert chunks == ["gate-a", "gate-b"] and len(log) == 1

    def env_parsing_defaults_and_bounds():
        for env_name in env_names:
            os.environ.pop(env_name, None)
        assert direct.openai_direct_private_enabled() is False
        assert (direct.openai_direct_model(), direct.openai_direct_reasoning_effort(), direct._timeout_s()) == (
            "gpt-5.6-terra", "none", 30.0)
        assert direct._endpoint() == "https://api.openai.com/v1/chat/completions"
        os.environ.update({"NANA_PRIVATE_LLM_PROVIDER": " OpenAI_Direct ", "NANA_OPENAI_DIRECT_REASONING_EFFORT": "LOW",
                           "NANA_OPENAI_DIRECT_MODEL": "gpt-6-luna", "NANA_OPENAI_DIRECT_TIMEOUT_S": "999"})
        assert direct.openai_direct_private_enabled() is True
        assert (direct.openai_direct_model(), direct.openai_direct_reasoning_effort(), direct._timeout_s()) == (
            "gpt-6-luna", "low", 120.0)
        os.environ.update({"NANA_PRIVATE_LLM_PROVIDER": "bogus", "NANA_OPENAI_DIRECT_REASONING_EFFORT": "turbo",
                           "NANA_OPENAI_DIRECT_TIMEOUT_S": "abc"})
        assert direct.openai_direct_private_enabled() is False
        assert (direct.openai_direct_reasoning_effort(), direct._timeout_s()) == ("none", 30.0)

    def route_snapshot_and_startup_label():
        snap = direct.private_route_snapshot("gemini-3-flash")
        assert snap["provider"] == "openai_direct" and snap["transport_model"] == "openai:gpt-5.6-terra", snap
        assert snap["fallback"] == "llmgate:gemini-3-flash" and snap["endpoint_host"] == "openai.invalid", snap
        assert snap["key_present"] is True, snap
        os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "llmgate"
        snap = direct.private_route_snapshot("gemini-3-flash")
        assert snap["provider"] == "llmgate" and snap["transport_model"] == "gemini-3-flash", snap
        import nana.runtime.startup_config_contract as contract

        def label(env):
            fields = contract._private_llm_route_fields(env, "gemini-3-flash")
            return contract._private_llm_label(types.SimpleNamespace(main_model="gemini-3-flash", **fields)), fields

        assert label({})[0] == "llmgate:gemini-3-flash"
        text, fields = label({"NANA_PRIVATE_LLM_PROVIDER": "OpenAI_Direct"})
        assert text == "openai_direct:gpt-5.6-terra/effort=none,fallback=llmgate:gemini-3-flash", text
        assert fields["private_llm_provider_raw_valid"] and fields["private_llm_reasoning_effort_raw_valid"]
        text, fields = label({"NANA_PRIVATE_LLM_PROVIDER": "bogus", "NANA_OPENAI_DIRECT_REASONING_EFFORT": "turbo"})
        assert text == "llmgate:gemini-3-flash" and not fields["private_llm_provider_raw_valid"], (text, fields)
        assert not fields["private_llm_reasoning_effort_raw_valid"], fields

    def llm_provider_session_command():
        ok, msg = direct.set_private_route_for_session("llmgate")
        assert ok and os.environ["NANA_PRIVATE_LLM_PROVIDER"] == "llmgate", msg
        ok, msg = direct.set_private_route_for_session("OpenAI_Direct", "gpt-6-luna", "LOW")
        assert ok and direct.openai_direct_private_enabled(), msg
        assert (direct.openai_direct_model(), direct.openai_direct_reasoning_effort()) == ("gpt-6-luna", "low")
        for bad in (("bogus",), ("openai_direct", "bad model!"), ("openai_direct", "gpt-6-luna", "turbo"),
                    ("llmgate", "gpt-6-luna")):
            before = dict(os.environ)
            ok, _msg = direct.set_private_route_for_session(*bad)
            assert not ok and dict(os.environ) == before, bad
        os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "llmgate"
        config.OPENAI_API_KEY = "OPENAI_KEY_CUA_BAN"
        ok, msg = direct.set_private_route_for_session("openai_direct")
        assert not ok and os.environ["NANA_PRIVATE_LLM_PROVIDER"] == "llmgate", msg
        config.OPENAI_API_KEY = "fixture-openai-key"
        lines = direct.private_route_command_lines("/llm-provider openai_direct gpt-5.6-terra none", "gemini-3-flash")
        assert lines[1].startswith("  Changed: private chat -> openai_direct gpt-5.6-terra"), lines
        assert lines[2].startswith("  Active: openai_direct -> gpt-5.6-terra | effort=none"), lines
        lines = direct.private_route_command_lines("/llm-provider", "gemini-3-flash")
        assert lines[1].startswith("  Active: openai_direct"), lines
        lines = direct.private_route_command_lines("/llm-provider llmgate a b c", "gemini-3-flash")
        assert lines[1].startswith("  Not changed: usage"), lines

    def llm_provider_extra_args_change_nothing():
        # Regression (Wiki coordinator review 2026-10-05): a rejected command must
        # leave provider, model and effort exactly as they were.
        route_keys = ("NANA_PRIVATE_LLM_PROVIDER", "NANA_OPENAI_DIRECT_MODEL", "NANA_OPENAI_DIRECT_REASONING_EFFORT")
        os.environ["NANA_PRIVATE_LLM_PROVIDER"] = "llmgate"
        os.environ.pop("NANA_OPENAI_DIRECT_MODEL", None)
        os.environ.pop("NANA_OPENAI_DIRECT_REASONING_EFFORT", None)
        for command in (
            "/llm-provider openai_direct gpt-6-luna low extra",
            "/llm-provider openai_direct gpt-6-luna low extra more",
            "/llm-provider bogus",
            "/llm-provider openai_direct gpt-6-luna turbo",
            "/llm-provider openai_direct bad!model",
            "/llm-provider llmgate gpt-6-luna",
        ):
            before = {key: os.environ.get(key) for key in route_keys}
            lines = direct.private_route_command_lines(command, "gemini-3-flash")
            after = {key: os.environ.get(key) for key in route_keys}
            assert lines[1].startswith("  Not changed: "), (command, lines)
            assert after == before, (command, before, after)
            assert lines[2] == "  Active: llmgate -> gemini-3-flash", (command, lines)

    def public_firewall_blocks_llm_provider():
        import nana.runtime.public_stage_identity as stage

        guard = stage.get_public_stage_identity_guard()
        assert guard.classify_public_input("/llm-provider llmgate") == "backstage_command"
        assert guard.classify_public_input("/LLM-PROVIDER") == "backstage_command"
        from nana.commands.registry_live import LIVE_SLASH_COMMANDS
        assert "/llm-provider" in LIVE_SLASH_COMMANDS

    for name, fn in (
        ("llm_provider_session_command", llm_provider_session_command),
        ("llm_provider_extra_args_change_nothing", llm_provider_extra_args_change_nothing),
        ("public_firewall_blocks_llm_provider", public_firewall_blocks_llm_provider),
        ("route_snapshot_and_startup_label", route_snapshot_and_startup_label),
        ("payload_uses_max_completion_tokens_and_effort_temperature_rule", payload_rules),
        ("sync_success_returns_content", sync_ok),
        ("sync_http_error_returns_none_for_fallback", sync_http_error_falls_back),
        ("placeholder_key_never_calls_network", placeholder_key_never_calls_network),
        ("stream_decodes_utf8_chunks_and_closes", stream_utf8_chunks),
        ("stream_provider_usage_reaches_route_telemetry", stream_usage_telemetry),
        ("stream_error_before_text_raises_unavailable", stream_error_before_text_raises),
        ("stream_cut_after_text_ends_without_replay", stream_cut_after_text_ends_quietly),
        ("boundary_gate_private_owner_only", boundary_scope_gate),
        ("disabled_flag_keeps_llmgate_stream_unchanged", disabled_returns_llmgate_untouched),
        ("enabled_private_stream_uses_direct_only", enabled_uses_direct_only),
        ("enabled_failure_before_text_falls_back_to_llmgate", enabled_failure_before_text_falls_back_to_llmgate),
        ("public_boundary_never_uses_direct", public_boundary_never_uses_direct),
        ("env_parsing_defaults_and_bounds", env_parsing_defaults_and_bounds),
    ):
        check(name, fn)

    for env_name, value in saved_env.items():
        if value is None:
            os.environ.pop(env_name, None)
        else:
            os.environ[env_name] = value
    failed = sum(1 for _name, exc in results if exc is not None)
    print(f"smoke_openai_direct_private_route: {len(results) - failed}/{len(results)} passed, network_calls=0")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
