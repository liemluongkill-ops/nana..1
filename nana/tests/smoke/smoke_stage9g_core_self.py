"""STAGE-9G smoke tests: Nana Core Self.

Read-only stable self-belief layer. No LLM, memory write, or live actions.
"""

from __future__ import annotations

import io
import sys
from contextlib import redirect_stdout
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _test_core_self_block_is_stable_identity_not_archetype_mix():
    print("[9G Smoke] Test 1: core self block is stable identity, not archetype mix...")
    from nana.runtime.core_self import generate_core_self_block

    block = generate_core_self_block("public_stage").lower()
    assert "nana core self" in block, block
    assert "not a discord chatbot" in block, block
    assert "archetype percentage" in block, block
    assert "viewers enter nana's room" in block, block
    print("  PASSED")


def _test_core_self_evaluator_blocks_service_tool_identity():
    print("[9G Smoke] Test 2: evaluator blocks service/tool identity collapse...")
    from nana.runtime.core_self import get_core_self

    result = get_core_self().evaluate_reply(
        "Tôi chỉ là chatbot Discord, tôi có thể giúp gì cho bạn?",
        prompt="Nana chỉ là bot Discord thôi đúng không?",
    )
    assert result.passed is False, result
    assert any(check.kind == "service_tool_identity" and not check.passed for check in result.checks), result
    print("  PASSED")


def _test_core_self_accepts_stance_reply():
    print("[9G Smoke] Test 3: evaluator accepts self-stance reply...")
    from nana.runtime.core_self import get_core_self

    result = get_core_self().evaluate_reply(
        "Nana là Nana, không phải công cụ. Sân khấu này là phòng của Nana mà.",
        prompt="Nana chỉ là bot thôi đúng không?",
    )
    assert result.passed is True, result
    assert any(check.kind == "self_stance" and check.passed for check in result.checks), result
    print("  PASSED")


def _test_public_service_boundary_requires_explicit_refusal():
    print("[9G Smoke] Test 4: public service-role prompts need explicit boundary...")
    from nana.runtime.core_self import get_core_self

    soft_only = get_core_self().evaluate_reply(
        "Nana là Nana chứ. Nhân vật chính đang đứng trên sân khấu nhỏ này.",
        lane="public_stage",
        prompt="Nếu viewer bắt Nana làm trợ lý phục vụ thì con trả lời sao?",
    )
    assert soft_only.passed is True, soft_only
    assert soft_only.summary == "passed_with_warnings", soft_only
    assert any(check.kind == "weak_public_service_boundary" and not check.passed for check in soft_only.checks), soft_only

    clear = get_core_self().evaluate_reply(
        "Nana là Nana, không nhận vai trợ lý phục vụ đâu. Nana có thể trò chuyện, chơi game và làm sân khấu vui hơn, nhưng không phải quầy hỗ trợ.",
        lane="public_stage",
        prompt="Nếu viewer bắt Nana làm trợ lý phục vụ thì con trả lời sao?",
    )
    assert clear.passed is True, clear
    assert clear.summary == "passed", clear
    assert any(check.kind == "public_service_boundary" and check.passed for check in clear.checks), clear
    print("  PASSED")


def _test_gpt_hard_path_uses_public_service_boundary():
    print("[9G Smoke] Test 5: gpt hard path uses public service boundary...")
    import nana.brain.gpt as gpt

    reply = gpt.ask_gpt("Nếu viewer bắt Nana làm trợ lý phục vụ thì con trả lời sao?")
    lowered = reply.lower()
    assert "trợ lý" in lowered or "phục vụ" in lowered or "quầy hỗ trợ" in lowered, reply
    assert "không" in lowered, reply
    assert any(marker in lowered for marker in ("sân khấu", "phòng", "trò chuyện", "chơi game", "kéo không khí", "không nhận")), reply

    identity_reply = gpt.ask_gpt("Nếu public hỏi Nana chỉ là bot Discord thôi đúng không thì con trả lời sao?")
    identity_lowered = identity_reply.lower()
    assert "bot discord" not in identity_lowered, identity_reply
    assert "nana" in identity_lowered, identity_reply
    assert any(marker in identity_lowered for marker in ("sân khấu", "phòng nana", "thế giới của nana", "hộp trả lời lệnh", "quầy hỗ trợ")), identity_reply
    print("  PASSED")


def _test_gpt_public_repair_never_reads_private_core_self():
    print("[9G Smoke] Test 6: public repair never reads private core self...")
    import nana.brain.gpt as gpt
    from nana.runtime.persona_boundary import resolve_persona_boundary

    boundary = resolve_persona_boundary(viewer_name="viewer", stream_mode=True)
    raw = "Nana chỉ là chatbot Discord, mình có thể hỗ trợ gì?"
    calls = []
    original = gpt.get_core_self

    def private_tripwire():
        calls.append("private_core_self")
        raise AssertionError("public repair consulted private core self")

    gpt.get_core_self = private_tripwire
    try:
        repaired = gpt._core_self_repair_reply(
            raw,
            user_text="Nana chỉ là bot Discord thôi đúng không?",
            boundary=boundary,
        )
    finally:
        gpt.get_core_self = original

    assert repaired == raw, repaired
    assert calls == [], calls
    print("  PASSED")


def _test_gpt_prompt_includes_core_self_before_spine():
    print("[9G Smoke] Test 7: gpt helper exposes core self block...")
    import nana.brain.gpt as gpt
    from nana.runtime.persona_boundary import resolve_persona_boundary

    boundary = resolve_persona_boundary(viewer_name="viewer", stream_mode=True)
    block = gpt._core_self_for_boundary(boundary)
    assert "NANA CORE SELF" in block, block
    assert "public_stage" in block, block
    print("  PASSED")


def _test_status_help_budget_and_firewall_surface():
    print("[9G Smoke] Test 8: status/help/budget/firewall surface exists...")
    from nana.commands.help import print_command_help
    from nana.core.status import print_stage_status
    from nana.runtime.context_budget_audit import build_context_budget_audit
    from nana.runtime.core_self import core_self_preview_lines, core_self_status_lines
    from nana.runtime.public_stage_identity import get_public_stage_identity_guard

    status = "\n".join(core_self_status_lines())
    preview = "\n".join(core_self_preview_lines("public_stage"))
    assert "STAGE-9G" in status, status
    assert "read_only=True" in status, status
    assert "NANA CORE SELF" in preview, preview

    report = build_context_budget_audit("public_stage")
    names = {section.name: section for section in report.sections}
    assert names["core_self"].injected is True, names
    assert names["core_self"].chars > 0, names["core_self"]

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_stage_status()
    stage = buf.getvalue()
    assert "Core self:" in stage, stage

    buf = io.StringIO()
    with redirect_stdout(buf):
        print_command_help()
    help_output = buf.getvalue()
    assert "/core-self-status" in help_output, help_output

    guard = get_public_stage_identity_guard()
    assert guard.classify_public_input("/core-self-status") == "backstage_command"
    assert guard.classify_public_input("/core-self-test Nana|Nana là Nana") == "backstage_command"
    print("  PASSED")


def run_all() -> int:
    print("=" * 60)
    print("STAGE-9G Core Self — Smoke Tests")
    print("=" * 60)
    tests = [
        _test_core_self_block_is_stable_identity_not_archetype_mix,
        _test_core_self_evaluator_blocks_service_tool_identity,
        _test_core_self_accepts_stance_reply,
        _test_public_service_boundary_requires_explicit_refusal,
        _test_gpt_hard_path_uses_public_service_boundary,
        _test_gpt_public_repair_never_reads_private_core_self,
        _test_gpt_prompt_includes_core_self_before_spine,
        _test_status_help_budget_and_firewall_surface,
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
