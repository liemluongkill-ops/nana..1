"""nana.social.draft — social draft commands: print, queue, show, confirm, cancel, propose."""
import unicodedata
import time


def parse_int_arg(raw_value):
    try:
        return int(str(raw_value).strip())
    except (TypeError, ValueError):
        return None


def shorten_line(text, limit):
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def normalize_draft_duplicate_key(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower()
    text = " ".join(text.split())
    return text.strip(" \t\r\n.,!?;:，。！？…")


def normalize_slashy_command_text(text):
    return str(text or "").lstrip("/").strip()


def print_draft_queue():
    from nana.actions.drafts import social_drafts
    snapshot = social_drafts.snapshot()
    pending = snapshot["pending"]
    print("🧾 Social Draft Queue")
    if not pending:
        print("  Pending: none")
        last = snapshot["last_closed"]
        if last:
            status, item, closed_at = last
            print(f"  Last closed: {status} | #{item.id} | {item.intent} | {time.time() - closed_at:.1f}s ago")
        return
    for item in pending:
        print(
            f"  #{item.id} | {item.intent} | policy={item.policy} | "
            f"age={item.age_seconds():.1f}s | expires={item.expires_in_seconds():.1f}s"
        )
        print(f"    {shorten_line(item.draft, 110)}")


def find_recent_duplicate_draft(draft_text, max_age_seconds=180):
    from nana.actions.drafts import social_drafts
    key = normalize_draft_duplicate_key(draft_text)
    if not key:
        return None, None
    for item in reversed(social_drafts.list_pending()):
        if item.age_seconds() > max_age_seconds:
            continue
        if normalize_draft_duplicate_key(item.draft) == key:
            return item, "same_text_recent"
    return None, None


def print_draft_show(raw_id):
    from nana.actions.drafts import social_drafts
    draft_id = parse_int_arg(raw_id)
    print("🧾 Social Draft")
    if draft_id is None:
        print("  Status: invalid_id")
        return
    item = social_drafts.get(draft_id)
    if not item:
        print("  Status: not_found")
        return
    print_draft_item(item)


def print_draft_confirm(raw_id):
    from nana.actions.drafts import social_drafts
    draft_id = parse_int_arg(raw_id)
    print("✅ Draft Confirm")
    if draft_id is None:
        print("  Status: invalid_id")
        return
    item, status = social_drafts.confirm(draft_id)
    print(f"  Status: {status}")
    if not item:
        print("  Execute: skipped")
        return
    print(f"  ID: {item.id}")
    print(f"  Intent: {item.intent}")
    print("  Execute: preview_confirmed; không post, không type nếu autonomy lock đang bật.")
    print(f"  Draft: {item.draft}")
    propose_social_type_draft(item)


def print_draft_cancel(raw_id):
    from nana.actions.drafts import social_drafts
    draft_id = parse_int_arg(raw_id)
    print("🛑 Draft Cancel")
    if draft_id is None:
        print("  Status: invalid_id")
        return
    item, status = social_drafts.cancel(draft_id)
    print(f"  Status: {status}")
    if item:
        print(f"  ID: {item.id}")
        print(f"  Intent: {item.intent}")


def print_draft_item(item):
    print(f"  ID: {item.id}")
    print(f"  Status: {item.status}")
    print(f"  Intent: {item.intent}")
    print(f"  Policy: {item.policy}")
    print(f"  Needs confirm: {item.needs_confirm}")
    print(f"  Model: {item.model}")
    print(f"  Context level: {item.context_level}")
    print(f"  Age: {item.age_seconds():.1f}s")
    print(f"  Expires in: {item.expires_in_seconds():.1f}s")
    print(f"  Request: {item.request}")
    print(f"  Draft: {item.draft}")


def propose_social_type_draft(item):
    from nana.core.autonomy import autonomy_lock_block_reason
    from nana.core.context import broker_context_snapshot
    from nana.core.pending_actions import pending_actions
    lock_reason = autonomy_lock_block_reason("social.type_draft")
    if lock_reason:
        print("🧾 Type Draft Action")
        print("  Pending: not created")
        print(f"  Guard: autonomy_lock")
        print(f"  Reason: {lock_reason}")
        print("  Note: Phase 5-10 chỉ xác nhận nội dung draft; không nhập chữ vào composer.")
        return

    broker_context = broker_context_snapshot()
    context = dict(broker_context)
    context.update({
        "draft_id": item.id,
        "draft_intent": item.intent,
        "draft_policy": item.policy,
        "draft_model": item.model,
        "draft_context_level": item.context_level,
        "draft_request": item.request,
        "draft_text": item.draft,
        "social_type_review": True,
    })

    block_reason = social_type_context_block_reason(context)
    if block_reason:
        print("🧾 Type Draft Action")
        print("  Pending: not created")
        print(f"  Reason: {block_reason}")
        print("  Note: cần mở X/Facebook/Messenger trong Edge debug, chạy /br rồi tạo draft mới nếu muốn đi tiếp.")
        return

    pending = pending_actions.propose(
        action="social.type_draft",
        reason="draft_confirmed",
        context=context,
        ttl_seconds=90,
    )
    print("🧾 Type Draft Action Created")
    print(f"  ID: {pending.id}")
    print(f"  Action: {pending.action}")
    print(f"  Draft ID: {item.id}")
    print(f"  Expires in: {pending.expires_in_seconds():.1f}s")
    print(f"  Confirm: /action-confirm {pending.id}")
    print(f"  Cancel: /action-cancel {pending.id}")
    print("  Preview: /action-plan")
    print("  Note: /action-confirm chỉ nhập chữ vào composer đang focus; không bấm đăng.")


def social_type_context_block_reason(context):
    if not context.get("browser_available"):
        return "browser_unavailable"
    if not context.get("browser_fresh"):
        return "browser_snapshot_stale"
    url = (context.get("browser_url") or "").lower()
    kind = (context.get("browser_kind") or "").lower()
    social_domains = (
        "https://x.com/",
        "https://twitter.com/",
        "https://www.facebook.com/",
        "https://facebook.com/",
        "https://www.messenger.com/",
        "https://messenger.com/",
    )
    if not url:
        return "browser_url_missing"
    if kind != "social" and not url.startswith(social_domains):
        return "target_not_social_tab"
    return None


# ---------------------------------------------------------------------------
# social_request helpers
# ---------------------------------------------------------------------------

from nana.social.classifier import strip_accents_for_match


def social_request_has_clear_topic(text, broker_context=None, intent=None):
    lowered = (text or "").lower()
    normalized = strip_accents_for_match(lowered)
    match_text = f"{lowered} {normalized}"
    topic_markers = [
        "về ", "ve ",
        "bug", "nana", "phase", "code", "runtime", "b2",
    ]
    page_markers = [
        "này", "nay", "đây", "day",
        "trang này", "trang nay",
        "thread này", "thread nay",
        "tweet này", "tweet nay",
        "post này", "post nay",
        "bài này", "bai nay",
        "đoạn này", "doan nay",
    ]
    context_kind = ((broker_context or {}).get("browser_kind") or "").lower()
    if context_kind == "social" and intent in {"social.reply", "social.post"}:
        return False
    if context_kind == "social" and any(marker in match_text for marker in ["reply", "comment", "trả lời", "tra loi"]):
        return False
    return any(marker in match_text for marker in topic_markers) and not any(marker in match_text for marker in page_markers)


def social_request_needs_page_context(text):
    lowered = (text or "").lower()
    normalized = strip_accents_for_match(lowered)
    match_text = f"{lowered} {normalized}"
    markers = [
        "tweet này", "tweet nay",
        "post này", "post nay",
        "bài này", "bai nay",
        "thread này", "thread nay",
        "trang này", "trang nay",
        "reply", "comment", "trả lời", "tra loi",
    ]
    return any(marker in match_text for marker in markers)


def social_target_missing_reason(raw_text, broker_context, context_preview, vision_description=None, omit_page_context=False):
    if omit_page_context:
        return None
    if not social_request_needs_page_context(raw_text):
        return None
    if vision_description:
        return None
    kind = ((broker_context or {}).get("browser_kind") or "").lower()
    has_post = bool((broker_context or {}).get("browser_social_post_text"))
    has_vibe = bool((broker_context or {}).get("browser_social_vibe"))
    has_packet = bool((context_preview.packet or "").strip())
    if kind == "social" and (has_post or has_vibe or has_packet):
        return None
    if kind != "social":
        return f"browser_kind={kind or 'unknown'}"
    return "social_context_empty"


def choose_social_draft_models(raw_text, route):
    lowered = (raw_text or "").lower()
    if any(marker in lowered for marker in ["max công lực", "max cong luc", "5.5", "cực hạn", "cuc han"]):
        return ["gpt-5.5", "gpt-5.4"]
    return ["gemini-3.1-flash-lite", "gpt-5.4-mini"]


# ---------------------------------------------------------------------------
# print_social_draft_test
# ---------------------------------------------------------------------------

def print_social_draft_test(raw_text, vision_description=None):
    from nana.core.context import broker_context_snapshot
    from nana.intent.planner import plan_intent
    from nana.core.context_budget import build_context_budget_preview
    from nana.core.recovery import recovery_clear_social, recovery_notice
    from nana.core.routing import route_sidecar_task
    from nana.actions.drafts import social_drafts
    from nana.intent.priority import context_priority_policy, format_context_priority_summary
    from nana.core.vision import LAST_VISION_DESCRIPTION
    from nana.social.classifier import (
        build_social_draft_source,
        social_source_classification,
        detect_public_reaction_style,
    )
    from nana.social.prompts import (
        build_social_draft_prompt,
        call_social_draft_with_fallbacks,
        clean_social_draft,
        enrich_social_context_packet,
        social_draft_guard_note,
    )
    from nana.social.teo_lite import should_use_teo_lite, apply_teo_lite_public_reply
    from nana.social.guards import fallback_social_draft
    from nana.core.context_recovery import context_recovery_next_step, vision_text_reconcile_report

    raw_text = normalize_slashy_command_text(raw_text)
    broker_context = broker_context_snapshot()
    plan = plan_intent(raw_text, context=broker_context)
    omit_page_context = social_request_has_clear_topic(raw_text, broker_context=broker_context, intent=plan.intent)
    context_level = "L0" if omit_page_context else "L2"
    context_preview = build_context_budget_preview(broker_context, level=context_level)
    prompt_context_packet = "Context trang hiện tại được bỏ qua vì yêu cầu của Ba đã có topic rõ." if omit_page_context else enrich_social_context_packet(context_preview.packet, broker_context)
    if vision_description and not omit_page_context:
        prompt_context_packet = f"{prompt_context_packet}\nvision_description: {vision_description}"
    saved_context_level = "L0-omitted" if omit_page_context else context_preview.level
    draft_source = build_social_draft_source(
        raw_text,
        broker_context=broker_context,
        vision_description=vision_description,
    )
    media_mode, reaction_style, guard_hint = social_source_classification(
        draft_source,
        broker_context=broker_context,
        raw_text=raw_text,
        vision_description=vision_description,
    )
    priority_policy = context_priority_policy(
        context=broker_context,
        vision_description=vision_description,
    )
    print("📝 Social Draft Preview")
    print(f"  Plan status: {plan.status}")
    print(f"  Intent: {plan.intent}")
    print(f"  Policy: {plan.policy}")
    print(f"  Needs confirm: {plan.needs_confirm}")
    print(f"  Privacy risk: {plan.privacy_risk}")
    print(f"  Context level: {saved_context_level} ({context_preview.level_name})")
    print(f"  Context allowed: {context_preview.allowed_for_external_model}")
    if omit_page_context:
        print("  Context priority: omitted_by_clear_topic")
    else:
        print(
            "  Context priority: "
            f"{format_context_priority_summary(priority_policy)}"
        )
    if vision_description:
        reconcile = vision_text_reconcile_report(context=broker_context, vision_description=vision_description)
        print(f"  Reconcile: {reconcile['status']} | text={','.join(reconcile['text_groups']) or 'none'} | vision={','.join(reconcile['vision_groups']) or 'none'}")
        if reconcile["status"] == "conflict" and not omit_page_context:
            print("  Execute: skipped")
            print("  Guard: vision_text_conflict")
            print("  Recovery: Vision và text đang lệch nhóm tín hiệu. Ba crop lại đúng vùng hoặc dùng /social-draft-test để chỉ dựa vào post/title.")
            return
        print("  Vision: included")
    elif LAST_VISION_DESCRIPTION and time.time() - LAST_VISION_DESCRIPTION.get("time", 0) <= 180:
        print("  Vision: available_but_not_used (use /social-draft-vision)")
    print(f"  Classifier: media={media_mode} | reaction_style={reaction_style} | guard_hint={guard_hint}")
    teo_lite = should_use_teo_lite(plan.sanitized_text, broker_context, plan.intent)
    if teo_lite:
        print("  Flavor: Teo-lite")

    if plan.status == "blocked":
        print("  Execute: skipped")
        print(f"  Blocked by: {', '.join(plan.blocked_reasons) if plan.blocked_reasons else 'policy'}")
        print(f"  Sanitized input: {plan.sanitized_text or 'None'}")
        print(f"  Recovery: {recovery_notice('social_plan_blocked', detail=plan.reason, cooldown=False)}")
        return

    if plan.intent not in {"social.draft", "social.post", "social.reply"}:
        print("  Execute: skipped")
        print("  Reason: social_draft_only")
        print(f"  Hint: intent hiện tại là {plan.intent}, không phải nháp/post/reply social.")
        print(f"  Recovery: {recovery_notice('social_intent_mismatch', detail=plan.intent, cooldown=False)}")
        return

    missing_target = social_target_missing_reason(
        raw_text,
        broker_context,
        context_preview,
        vision_description=vision_description,
        omit_page_context=omit_page_context,
    )
    if missing_target:
        print("  Execute: skipped")
        print(f"  Reason: social_target_missing ({missing_target})")
        if guard_hint != "none":
            print(f"  Guard: {guard_hint}")
        print(
            "  Recovery: "
            f"{context_recovery_next_step(broker_context, missing_reason=missing_target, vision_description=vision_description)}"
        )
        return

    if not context_preview.allowed_for_external_model:
        print("  Execute: skipped")
        print(f"  Blocked by context: {', '.join(context_preview.blocked_reasons) if context_preview.blocked_reasons else 'context_not_allowed'}")
        print(f"  Recovery: {recovery_notice('social_context_blocked', detail=', '.join(context_preview.blocked_reasons), cooldown=False)}")
        return

    mark_social_residue()
    if vision_description:
        mark_residue("vision", level=15)

    route_text = "Soạn nháp social nhẹ cho Nana"
    route = route_sidecar_task(route_text, context=broker_context)
    print(f"  Route status: {route.status}")
    print(f"  Router model: {route.model}")
    print(f"  Fallbacks: {', '.join(route.fallback_models) if route.fallback_models else 'none'}")
    if route.status == "blocked":
        print("  Execute: skipped")
        print(f"  Blocked by: {', '.join(route.blocked_reasons)}")
        print(f"  Sanitized input: {route.sanitized_input or 'None'}")
        print(f"  Recovery: {recovery_notice('social_route_blocked', detail=', '.join(route.blocked_reasons), cooldown=False)}")
        return

    model_names = choose_social_draft_models(plan.sanitized_text, route)
    prompt = build_social_draft_prompt(
        user_text=route.sanitized_input,
        original_request=plan.sanitized_text,
        context_packet=prompt_context_packet,
        intent=plan.intent,
    )
    response, debug, used_model = call_social_draft_with_fallbacks(
        model_names,
        prompt,
    )
    draft = clean_social_draft(response, source_text=draft_source, intent=plan.intent)
    if teo_lite:
        draft = apply_teo_lite_public_reply(draft, source_text=draft_source, intent=plan.intent)
    guard_note = social_draft_guard_note(response, draft, source_text=draft_source, intent=plan.intent)
    print(f"  Draft model: {used_model}")
    print(f"  Helper debug: {debug}")
    if not response:
        print(f"  Recovery: {recovery_notice('social_draft_model_failed', detail=debug, cooldown=False)}")
    print("  Safety: draft_only, no_type, no_post, no_like_follow, Ba_confirm_before_public_action")
    if plan.needs_confirm:
        print("  Confirm note: đây chỉ là preview; đăng/reply thật vẫn cần Ba xác nhận.")
    final_draft = draft or fallback_social_draft(plan.intent, source_text=raw_text)
    duplicate, duplicate_reason = find_recent_duplicate_draft(final_draft)
    saved = social_drafts.add(
        intent=plan.intent,
        policy=plan.policy,
        draft=final_draft,
        request=plan.sanitized_text,
        model=used_model,
        context_level=saved_context_level,
        source_text=draft_source,
        reaction_style=reaction_style,
        guard_hint=guard_hint,
        context_priority=format_context_priority_summary(priority_policy),
        needs_confirm=plan.needs_confirm,
    )
    print(f"  Draft ID: {saved.id}")
    if duplicate:
        print(
            "  Duplicate warning: "
            f"{duplicate_reason} với draft #{duplicate.id} ({duplicate.age_seconds():.1f}s trước)."
        )
    if guard_note:
        print(f"  Guard: {guard_note}")
    print(f"  Draft: {final_draft}")
    print(f"  Queue: /draft-show {saved.id} | /draft-confirm {saved.id} | /draft-cancel {saved.id}")
    if response:
        cleared = recovery_clear_social(reason="social_draft_created")
        if cleared:
            print(f"  Recovery cleared: {', '.join(cleared)}")


def mark_social_residue():
    from nana.core.context import mark_residue
    mark_residue("social_draft", level=10)


def mark_residue(tag, level=5):
    from nana.core.context import runtime_queue
    runtime_queue.push({"type": "residue", "tag": tag, "level": level, "time": time.time()})
