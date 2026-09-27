"""Smoke tests for LLM route status and explicit probes."""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_snapshot_uses_public_route():
    print("[LLM Route] Test 1: snapshot exposes public/core routes...")
    from nana.runtime.llm_route_status import llm_route_snapshot

    snap = llm_route_snapshot()
    assert snap["read_only"] is True, snap
    assert snap["api_call"] is False, snap
    assert snap["public_primary"] == "gpt-5.4-mini", snap
    assert "gemini-3-flash" in snap["public_order"], snap
    assert "grok-4.20-0309-non-reasoning" in snap["public_order"], snap
    assert snap["core_primary"] == "gpt-5.6-terra", snap
    assert snap["core_reasoning_effort"] == "none", snap
    assert snap["prompt_budget"] == {
        "compact_private": True,
        "short_term_lines": 6,
        "recent_chat_lines": 8,
        "retrieval_limit": 3,
        "memory_rule_limit": 12,
        "chat_max_tokens": 420,
        "story_max_tokens": 1000,
    }, snap
    assert snap["transport"]["pooled"] is True, snap
    assert snap["private_fast"]["config"] == {
        "enabled": False,
        "model": "gemini-3-flash",
        "transport": "buffered_chat_completion",
        "max_input_chars": 220,
        "max_tokens": 96,
    }, snap
    print("  PASSED")


def _test_status_lines_are_read_only():
    print("[LLM Route] Test 2: status lines are read-only and no API...")
    from nana.runtime.llm_route_status import llm_route_status_lines

    text = "\n".join(llm_route_status_lines())
    assert "api_call=False" in text, text
    assert "status never calls API" in text, text
    assert "gpt-5.4-mini" in text, text
    assert "gpt-5.6-terra" in text, text
    assert "reasoning_effort=none" in text, text
    assert "compact_private=True" in text, text
    assert "Last transport:" in text and "pooled=True" in text, text
    assert "Private fast pilot:" in text, text
    assert "Core rolling latency:" in text, text
    assert "gemini-3-flash" in text, text
    assert "/llm-route-probe" in text, text
    print("  PASSED")


def _test_probe_uses_injected_caller():
    print("[LLM Route] Test 3: probe uses explicit caller and reports latency...")
    from nana.runtime.llm_route_status import llm_route_probe_lines, parse_probe_args

    calls = []

    def fake_caller(model, messages, max_tokens=90, temperature=0.55):
        calls.append((model, messages, max_tokens, temperature))
        return "Nana test route ok.", "ok"

    args = parse_probe_args("gemini-3-flash phong nay im qua")
    assert args.model == "gemini-3-flash", args
    assert "phong nay" in args.text, args

    text = "\n".join(llm_route_probe_lines("gemini-3-flash phong nay im qua", caller=fake_caller))
    assert calls and calls[0][0] == "gemini-3-flash", calls
    assert "OK: True" in text, text
    assert "Nana test route ok." in text, text
    assert "explicit API probe only" in text, text
    print("  PASSED")


def _test_bakeoff_uses_injected_caller_and_flags_quality():
    print("[LLM Route] Test 4: bakeoff ranks models and flags voice warnings...")
    from nana.runtime.llm_route_status import llm_route_bakeoff_lines, parse_bakeoff_args

    calls = []

    def fake_caller(model, messages, max_tokens=90, temperature=0.55):
        calls.append(model)
        replies = {
            "gemini-3-flash": "Nana kéo nhịp nhẹ thôi, phòng im nhưng sân khấu vẫn còn sáng.",
            "grok-4.20-0309-non-reasoning": "Mình nói chuyện gì cho vui đi nè? 😊",
            "gpt-5.4-mini": "Nana thử mở một câu chuyện nhỏ cho phòng đỡ im nhé.",
            "gemini-3.1-flash-lite": "Nana ở đây nè.",
        }
        return replies.get(model, "Nana test ok."), "ok"

    args = parse_bakeoff_args("--models=gemini-3-flash,grok-4.20-0309-non-reasoning hello")
    assert args.models == ["gemini-3-flash", "grok-4.20-0309-non-reasoning"], args
    assert args.text == "hello", args

    text = "\n".join(llm_route_bakeoff_lines("phong nay im qua", caller=fake_caller))
    assert len(calls) == 4, calls
    assert "LLM Route Bakeoff" in text, text
    assert "Suggested:" in text, text
    assert "grok-4.20-0309-non-reasoning" in text, text
    assert "uses_minh" in text and "emoji" in text, text
    assert "explicit multi-model API probe only" in text, text
    print("  PASSED")


def _test_bakeoff_rejects_foreign_identity_even_when_fastest():
    print("[LLM Route] Test 6: foreign provider identity is a hard reject...")
    from nana.runtime.llm_route_status import llm_route_bakeoff_lines

    def fake_caller(model, messages, max_tokens=90, temperature=0.55):
        if model == "claude-haiku-4-5":
            return (
                "I can't discuss that. I'm Kiro, an AI development environment.",
                "ok",
            )
        return "Con đây Ba, Nana nghe rõ rồi.", "ok"

    text = "\n".join(
        llm_route_bakeoff_lines(
            "--models=claude-haiku-4-5,gemini-3-flash alo",
            caller=fake_caller,
        )
    )
    assert "foreign_identity" in text, text
    assert "identity_refusal" in text, text
    assert "Suggested: gemini-3-flash" in text, text
    print("  PASSED")


def _test_status_help_stage_and_firewall_surface():
    print("[LLM Route] Test 5: status/help/stage/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_llm_route_status, print_stage_status
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_llm_route_status()
    status = buf.getvalue()
    assert "LLM Route Status" in status, status
    assert "gemini-3-flash" in status, status

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "LLM route:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/llm-route-status" in help_output, help_output
    assert "/llm-route-probe" in help_output, help_output
    assert "/llm-route-bakeoff" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/llm-route-status") == "backstage_command"
    assert guard.classify_public_input("/llm-route-probe gemini-3-flash hi") == "backstage_command"
    assert guard.classify_public_input("/llm-route-bakeoff hi") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("LLM Route Status — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_snapshot_uses_public_route,
        _test_status_lines_are_read_only,
        _test_probe_uses_injected_caller,
        _test_bakeoff_uses_injected_caller_and_flags_quality,
        _test_status_help_stage_and_firewall_surface,
        _test_bakeoff_rejects_foreign_identity_even_when_fastest,
    ]
    failed = 0
    for test in tests:
        try:
            test()
        except Exception as exc:
            failed += 1
            print(f"  FAILED: {type(exc).__name__}: {exc}")
    passed = len(tests) - failed
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(run_all())
