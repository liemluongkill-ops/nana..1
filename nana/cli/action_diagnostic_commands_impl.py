"""Action/admin/diagnostic command router for Nana CLI.

This module keeps backstage diagnostics out of the main chat dispatcher. It is
still read-only unless the underlying legacy command already had a local
side-effect such as confirming a pending dry-run action or clearing vision cache.
"""

from __future__ import annotations

from importlib import import_module

from nana.core.action_flow import (
    cancel_pending_action,
    confirm_pending_action,
    parse_action_id,
    print_broker_decision,
    print_next_step_suggestion,
    print_pending_action,
    print_pending_action_plan,
    propose_action,
)
from nana.runtime.context import reset_proactive_state, set_proactive_enabled
from nana.runtime.persona import reset_presence_rhythm, set_presence_rhythm_enabled
from nana.runtime.proactive import print_phase3_state, print_phase4_state
from nana.social.classify import print_classify_expect, print_classify_text, print_social_classify
from nana.social.draft import (
    print_draft_cancel,
    print_draft_confirm,
    print_draft_queue,
    print_draft_show,
    print_social_draft_test,
)
from nana.social.quality import print_draft_quality, print_draft_quality_test
from nana.social.status import (
    print_social_guard_failures,
    print_social_guard_status,
    print_social_policy,
    print_social_target_status,
)
from nana.social.vision import (
    clear_vision_preview_cache,
    print_social_draft_vision,
    print_vision_cache_state,
    print_vision_describe,
    print_vision_preview,
)


def _diagnostic(name: str):
    return getattr(import_module("nana.core.diagnostics"), name)


def _browser_refresh(name: str):
    return getattr(import_module("nana.runtime.browser_refresh"), name)


async def handle_action_diagnostic_command(loop, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()

    if text_lower == "/browser":
        _browser_refresh("print_browser_state")()
        return True

    if text_lower == "/phase3":
        print_phase3_state()
        return True

    if text_lower == "/phase4":
        print_phase4_state()
        return True

    if text_lower == "/queue":
        from nana.core.status_runtime import print_queue_state

        print_queue_state()
        return True

    if text_lower == "/actions":
        _diagnostic("print_action_registry")()
        return True

    if text_lower == "/broker-test":
        print("⚠️ Thiếu action. Ví dụ: /broker-test browser.click")
        return True

    if text_lower.startswith("/broker-test "):
        action_name = text.replace("/broker-test", "", 1).strip()
        print_broker_decision(action_name)
        return True

    if text_lower == "/broker-test-edge":
        print("⚠️ Thiếu action. Ví dụ: /broker-test-edge browser.click")
        return True

    if text_lower.startswith("/broker-test-edge "):
        action_name = text.replace("/broker-test-edge", "", 1).strip()
        print_broker_decision(action_name, force_edge=True)
        return True

    if text_lower == "/action-propose":
        print("⚠️ Thiếu action. Ví dụ: /action-propose browser.scroll")
        return True

    if text_lower.startswith("/action-propose "):
        action_name = text.replace("/action-propose", "", 1).strip()
        propose_action(action_name)
        return True

    if text_lower == "/action-propose-edge":
        print("⚠️ Thiếu action. Ví dụ: /action-propose-edge browser.scroll")
        return True

    if text_lower.startswith("/action-propose-edge "):
        action_name = text.replace("/action-propose-edge", "", 1).strip()
        propose_action(action_name, force_edge=True)
        return True

    if text_lower == "/pending-action":
        print_pending_action()
        return True

    if text_lower in {"/pending", "/pa"}:
        print_pending_action()
        return True

    if text_lower == "/action-plan":
        print_pending_action_plan()
        return True

    if text_lower in {"/suggest", "/next-step"}:
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="suggest_cache")
        print_next_step_suggestion()
        return True

    if text_lower == "/privacy-test":
        sample = (
            "OPENAI_API_KEY=" + "sk-" + "test1234567890abcdef "
            "email ba@example.com token=" + "fixture-token" + " phone 0912345678"
        )
        _diagnostic("print_privacy_report")(sample)
        return True

    if text_lower.startswith("/privacy-test "):
        raw_text = text.replace("/privacy-test", "", 1).strip()
        _diagnostic("print_privacy_report")(raw_text)
        return True

    if text_lower.startswith("/context-preview") or text_lower.startswith("/context-budget"):
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="context_preview_cache")
        preview_level = _diagnostic("parse_context_preview_level")(text_lower)
        _diagnostic("print_context_budget_preview")(level=preview_level)
        return True

    if text_lower == "/context-confidence":
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="context_confidence_cache")
        _diagnostic("print_context_confidence")()
        return True

    if text_lower == "/context-priority":
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="context_priority_cache")
        _diagnostic("print_context_priority")()
        return True

    if text_lower == "/context-recovery":
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="context_recovery_cache")
        _diagnostic("print_context_recovery")()
        return True

    if text_lower in {"/video-context", "/vc"}:
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="video_context_cache")
        _diagnostic("print_video_context")()
        return True

    if text_lower in {"/evidence-trace", "/context-evidence"}:
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="evidence_trace_cache")
        _diagnostic("print_evidence_trace")()
        return True

    if text_lower == "/vision-preview":
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="vision_preview_cache")
        print_vision_preview()
        return True

    if text_lower == "/vision-describe":
        print_vision_describe()
        return True

    if text_lower == "/reconcile-check":
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="reconcile_check_cache")
        _diagnostic("print_reconcile_check")()
        return True

    if text_lower == "/reconcile-expect":
        print("⚠️ Thiếu case. Ví dụ: /reconcile-expect aligned | Honkai Star Rail lofi remix || ảnh anime cô gái")
        return True

    if text_lower.startswith("/reconcile-expect "):
        raw_text = text.replace("/reconcile-expect", "", 1).strip()
        _diagnostic("print_reconcile_expect")(raw_text)
        return True

    if text_lower in {"/reconcile-guard-status", "/reconcile-guards"}:
        _diagnostic("print_reconcile_guard_status")()
        return True

    if text_lower == "/vision-cache":
        print_vision_cache_state()
        return True

    if text_lower == "/vision-cache-clear":
        removed = clear_vision_preview_cache()
        print(f"🧹 Vision cache cleared: {removed} file(s)")
        print_vision_cache_state()
        return True

    if text_lower.lstrip("/") == "social-draft-vision":
        print("⚠️ Thiếu câu. Ví dụ: /social-draft-vision Nana viết nháp reply nhẹ nhàng cho tweet này")
        return True

    if text_lower.startswith("/social-draft-vision "):
        raw_text = text.split("social-draft-vision", 1)[1].strip()
        print_social_draft_vision(raw_text)
        return True

    if text_lower == "/route-test":
        print("⚠️ Thiếu text. Ví dụ: /route-test tóm tắt trang này thật ngắn")
        return True

    if text_lower.startswith("/route-test "):
        raw_text = text.replace("/route-test", "", 1).strip()
        _diagnostic("print_route_test")(raw_text)
        return True

    if text_lower == "/llmgate-test":
        print("⚠️ Thiếu text. Ví dụ: /llmgate-test Reply with exactly: LLMGate OK")
        return True

    if text_lower.startswith("/llmgate-test "):
        raw_text = text.replace("/llmgate-test", "", 1).strip()
        _diagnostic("print_llmgate_test")(raw_text)
        return True

    if text_lower == "/intent-test":
        print("⚠️ Thiếu câu. Ví dụ: /intent-test Nana soạn nháp tin nhắn trả lời bạn này")
        return True

    if text_lower.startswith("/intent-test "):
        raw_text = text.replace("/intent-test", "", 1).strip()
        _diagnostic("print_intent_test")(raw_text)
        return True

    if text_lower == "/social-policy":
        print_social_policy()
        return True

    if text_lower in {"/social-target", "/target-status"}:
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="social_target_cache")
        print_social_target_status()
        return True

    if text_lower == "/social-classify":
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="social_classify_cache")
        print_social_classify()
        return True

    if text_lower.startswith("/social-classify "):
        raw_text = text.replace("/social-classify", "", 1).strip()
        await _browser_refresh("ensure_browser_snapshot")(loop, reason="social_classify_cache")
        print_social_classify(raw_text)
        return True

    if text_lower == "/social-guard-status":
        print_social_guard_status()
        return True

    if text_lower in {"/social-guard-failures", "/social-guard-fails"}:
        print_social_guard_failures()
        return True

    if text_lower == "/classify-text":
        print("⚠️ Thiếu text. Ví dụ: /classify-text video xe máy tông cột điện trong hẻm")
        return True

    if text_lower.startswith("/classify-text "):
        raw_text = text.replace("/classify-text", "", 1).strip()
        print_classify_text(raw_text)
        return True

    if text_lower == "/classify-expect":
        print("⚠️ Thiếu case. Ví dụ: /classify-expect danger | video xe máy tông cột điện trong hẻm")
        return True

    if text_lower.startswith("/classify-expect "):
        raw_text = text.replace("/classify-expect", "", 1).strip()
        print_classify_expect(raw_text)
        return True

    if text_lower == "/draft-quality-test":
        print("⚠️ Thiếu case. Ví dụ: /draft-quality-test video xe máy tông cột điện || Cute thế.")
        return True

    if text_lower.startswith("/draft-quality-test "):
        raw_text = text.replace("/draft-quality-test", "", 1).strip()
        print_draft_quality_test(raw_text)
        return True

    if text_lower.lstrip("/") == "social-draft-test":
        print("⚠️ Thiếu câu. Ví dụ: /social-draft-test Nana soạn nháp tweet về bug này")
        return True

    if text_lower.startswith("/social-draft-test "):
        raw_text = text.split("social-draft-test", 1)[1].strip()
        print_social_draft_test(raw_text)
        return True

    if text_lower == "/drafts":
        print_draft_queue()
        return True

    if text_lower in {"/draft-quality", "/draft-quality-gate"}:
        print_draft_quality()
        return True

    if text_lower.startswith("/draft-quality ") or text_lower.startswith("/draft-quality-gate "):
        raw_id = text.split(" ", 1)[1]
        print_draft_quality(raw_id)
        return True

    if text_lower == "/draft-show":
        print("⚠️ Thiếu ID. Ví dụ: /draft-show 1")
        return True

    if text_lower.startswith("/draft-show "):
        raw_id = text.split(" ", 1)[1]
        print_draft_show(raw_id)
        return True

    if text_lower == "/draft-confirm":
        print("⚠️ Thiếu ID. Ví dụ: /draft-confirm 1")
        return True

    if text_lower.startswith("/draft-confirm "):
        raw_id = text.split(" ", 1)[1]
        print_draft_confirm(raw_id)
        return True

    if text_lower == "/draft-cancel":
        print("⚠️ Thiếu ID. Ví dụ: /draft-cancel 1")
        return True

    if text_lower.startswith("/draft-cancel "):
        raw_id = text.split(" ", 1)[1]
        print_draft_cancel(raw_id)
        return True

    if text_lower == "/refine-test":
        print("⚠️ Thiếu câu nháp. Ví dụ: /refine-test Trang này là video YouTube của bài hát X.")
        return True

    if text_lower.startswith("/refine-test "):
        raw_text = text.replace("/refine-test", "", 1).strip()
        _diagnostic("print_refine_test")(raw_text)
        return True

    if text_lower == "/refine-guard-test":
        print("⚠️ Thiếu câu nháp. Ví dụ: /refine-guard-test Nếu Ba cần gì thì cứ nói nhé.")
        return True

    if text_lower.startswith("/refine-guard-test "):
        raw_text = text.replace("/refine-guard-test", "", 1).strip()
        _diagnostic("print_refine_guard_test")(raw_text)
        return True

    if text_lower == "/refine-auto-test":
        print("⚠️ Thiếu câu nháp. Ví dụ: /refine-auto-test Nếu Ba cần gì thì cứ nói nhé.")
        return True

    if text_lower.startswith("/refine-auto-test "):
        raw_text = text.replace("/refine-auto-test", "", 1).strip()
        _diagnostic("print_refine_auto_test")(raw_text)
        return True

    if text_lower.startswith("/action-confirm") or text_lower.startswith("/confirm"):
        action_id = parse_action_id(
            text.replace("/action-confirm", "", 1)
            if text_lower.startswith("/action-confirm")
            else text.replace("/confirm", "", 1)
        )
        confirm_pending_action(action_id)
        return True

    if text_lower.startswith("/action-cancel") or text_lower.startswith("/cancel"):
        action_id = parse_action_id(
            text.replace("/action-cancel", "", 1)
            if text_lower.startswith("/action-cancel")
            else text.replace("/cancel", "", 1)
        )
        cancel_pending_action(action_id)
        return True

    if text_lower == "/dom":
        _diagnostic("print_dom_state")()
        return True

    if text_lower in ["/focus", "/fx"]:
        await _browser_refresh("refresh_browser_state")(loop, show=False, reason="manual_focus", force=True)
        from nana.core.status_runtime import print_focus_state

        print_focus_state()
        return True

    if text_lower == "/presence":
        from nana.core.status_runtime import print_presence_state

        print_presence_state()
        return True

    if text_lower in {"/vts-expression-policy", "/expression-policy"}:
        from nana.core.status_runtime import print_vts_expression_policy_status

        print_vts_expression_policy_status()
        return True

    if text_lower == "/speech-shape-test":
        _diagnostic("print_speech_shape_test")()
        return True

    if text_lower.startswith("/speech-shape-test "):
        _diagnostic("print_speech_shape_test")(text.split(" ", 1)[1])
        return True

    if text_lower == "/time":
        from nana.core.status_runtime import print_time_state

        print_time_state()
        return True

    if text_lower == "/reaction-test":
        print("⚠️ Thiếu loại test. Ví dụ: /reaction-test idle")
        return True

    if text_lower.startswith("/reaction-test "):
        kind = text_lower.replace("/reaction-test", "", 1).strip()
        _diagnostic("print_reaction_test")(kind)
        return True

    if text_lower == "/presence-on":
        set_proactive_enabled(True)
        set_presence_rhythm_enabled(True)
        print("🟢 Presence đã bật")
        from nana.core.status_runtime import print_presence_state

        print_presence_state()
        return True

    if text_lower == "/presence-off":
        set_proactive_enabled(False)
        set_presence_rhythm_enabled(False)
        print("💤 Presence đã tắt")
        from nana.core.status_runtime import print_presence_state

        print_presence_state()
        return True

    if text_lower == "/presence-reset":
        reset_proactive_state()
        reset_presence_rhythm()
        print("🔄 Presence đã reset")
        from nana.core.status_runtime import print_presence_state

        print_presence_state()
        return True

    if text_lower in ["/browser-refresh-deep", "/br-deep"]:
        await _browser_refresh("refresh_browser_state")(loop, reason="manual_br_deep", deep=True, force=True)
        return True

    if text_lower in ["/browser-refresh", "/br"]:
        await _browser_refresh("refresh_browser_state")(loop, reason="manual_br", deep=False, force=True)
        return True

    return False
