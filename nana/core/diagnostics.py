"""Core diagnostic command facades for the new Nana runtime.

These helpers keep slash-command output out of ``nana.main`` while delegating
the actual policy decisions to the smaller core modules.
"""
from __future__ import annotations

import time

from nana.actions.privacy import build_privacy_report
from nana.actions.registry import action_registry
from nana.core.context import broker_context_snapshot
from nana.core.context_budget import build_context_budget_preview
from nana.core.context_recovery import context_recovery_next_step, vision_text_reconcile_report
from nana.core.format import shorten_line
from nana.core.vision import LAST_VISION_DESCRIPTION
from nana.intent.planner import plan_intent
from nana.intent.priority import context_priority_policy, format_context_priority_summary
from nana.memory import memory, memory_lock
from nana.runtime.metrics import RUNTIME_RECONCILE
from nana.runtime.context import context_snapshot


def is_diagnostic_fragment(text):
    normalized = " ".join(str(text or "").split()).strip()
    lowered = normalized.lower()
    if not normalized:
        return False
    prefixes = [
        "execute:",
        "reason:",
        "recovery:",
        "draft id:",
        "draft:",
        "queue:",
        "status:",
        "context level:",
        "context allowed:",
        "plan status:",
        "intent:",
        "policy:",
        "needs confirm:",
        "privacy risk:",
        "route status:",
        "router model:",
        "helper debug:",
        "safety:",
        "confirm note:",
        "duplicate warning:",
    ]
    if any(lowered.startswith(prefix) for prefix in prefixes):
        return True
    exact_lines = {
        "execute: skipped",
        "reason: social_target_missing (...)",
        "reason: no_vision_description",
        "reason: no_preview_image",
    }
    return lowered in exact_lines


def print_reaction_test(kind):
    from nana.brain.reaction_composer import compose_reaction

    snapshot = context_snapshot()
    browser = dict(snapshot.get("browser", {}))
    with memory_lock:
        emotion = dict(memory.get("emotion", {}))
    zone = snapshot.get("active_zone")
    normalized = {
        "idle": "idle_relaxed",
        "afk": "idle_relaxed",
        "yt": "youtube",
        "ai": "ai_tools",
    }.get(kind, kind)
    event = "browser_presence" if normalized in {"youtube", "music", "video", "shopping", "ai_tools"} else normalized
    browser_kind = normalized if event == "browser_presence" else None
    reaction, debug = compose_reaction(
        event=event,
        zone=zone,
        browser_kind=browser_kind,
        emotion=emotion,
        title=browser.get("title"),
    )
    print("🧪 Reaction test")
    print(f"  Input: {kind}")
    print(f"  Event: {event}")
    print(f"  Browser kind: {browser_kind}")
    print(f"  Debug: {debug}")
    print(f"  Line: {reaction}")


def parse_context_preview_level(text_lower):
    parts = str(text_lower or "").split()
    for part in parts[1:]:
        candidate = part.upper()
        if candidate in {"L0", "L1", "L2", "L3", "L4"}:
            return candidate
        if candidate.startswith("--LEVEL="):
            value = candidate.replace("--LEVEL=", "", 1)
            if value in {"L0", "L1", "L2", "L3", "L4"}:
                return value
    return None


def print_privacy_report(raw_text):
    report = build_privacy_report(raw_text, source="manual")
    print("🔐 Privacy Gate")
    print(f"  Source: {report.source}")
    print(f"  Risk: {report.risk}")
    print(f"  External model allowed: {report.allowed_for_external_model}")
    print(f"  Chars: {report.original_chars} -> {report.sanitized_chars}")
    print(f"  Findings: {_format_findings(report.findings)}")
    if report.blocked_reasons:
        print(f"  Blocked by: {', '.join(report.blocked_reasons)}")
    print(f"  Sanitized: {report.sanitized_text or 'None'}")


def print_context_budget_preview(force_edge=False, level=None):
    context = broker_context_snapshot(force_edge=force_edge)
    preview = build_context_budget_preview(context, level=level)
    print("🧱 Context Budget")
    print(f"  Level: {preview.level} ({preview.level_name})")
    print(f"  Max chars: {preview.max_chars}")
    print(f"  Used chars: {preview.used_chars}")
    print(f"  Risk: {preview.risk}")
    print(f"  External model allowed: {preview.allowed_for_external_model}")
    print(f"  Requires confirm: {preview.requires_confirm}")
    print(f"  Included: {', '.join(preview.included) if preview.included else 'none'}")
    print(f"  Dropped: {', '.join(preview.dropped) if preview.dropped else 'none'}")
    print(f"  Privacy findings: {_format_findings(preview.findings)}")
    if preview.blocked_reasons:
        print(f"  Blocked by: {', '.join(preview.blocked_reasons)}")
    print("  Packet:")
    if preview.packet:
        for line in preview.packet.splitlines():
            print(f"    {line}")
    else:
        print("    None")


def print_context_confidence(force_edge=False):
    context = broker_context_snapshot(force_edge=force_edge)
    policy = context_priority_policy(context=context, vision_description=_fresh_vision_text())
    age = context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"
    overall = float(policy.get("overall") or 0.0)
    usable = policy.get("ranked") or []
    primary = usable[0].get("name") if usable else "none"
    secondary = usable[1].get("name") if len(usable) > 1 else "none"
    print("🧭 Context Confidence")
    print(
        "  Browser: "
        f"available={context.get('browser_available')} | fresh={context.get('browser_fresh')} | "
        f"state={context.get('browser_snapshot_state') or 'INVALID'} | age={age_text} | "
        f"kind={context.get('browser_kind')}"
    )
    print(f"  Overall: {overall:.2f} ({_confidence_label(overall)})")
    print(f"  Primary: {primary} | Secondary: {secondary}")
    print("  Sources:")
    for row in policy.get("rows") or []:
        status = "available" if row.get("available") else "missing"
        score = float(row.get("score") or 0.0)
        print(f"    {row.get('name')}: {score:.2f} ({_confidence_label(score)}) | {status}")
    print("  Rule: post/title/URL là xương sống; vision/vibe/comments chỉ nâng hoặc hạ độ chắc.")


def print_context_priority():
    context = broker_context_snapshot()
    policy = context_priority_policy(context=context, vision_description=_fresh_vision_text())
    age = policy.get("age")
    age_text = "None" if age is None else f"{age:.1f}s"
    overall = float(policy.get("overall") or 0.0)
    print("🧭 Context Priority")
    print(
        "  Browser: "
        f"kind={policy.get('kind')} | media={policy.get('media_mode')} | "
        f"state={policy.get('state')} | fresh={policy.get('fresh')} | age={age_text} | "
        f"overall={overall:.2f} ({_confidence_label(overall)})"
    )
    print(f"  Policy: {policy.get('reason')}")
    print("  Use order:")
    ranked = policy.get("ranked") or []
    if not ranked:
        print("    none")
    for index, row in enumerate(ranked, start=1):
        print(f"    P{index} {row.get('name')} | score={float(row.get('score') or 0.0):.2f}")
    ignored = policy.get("ignored") or []
    print("  Ignored/missing:")
    if not ignored:
        print("    none")
    for row in ignored:
        print(f"    {row.get('name')} | {_confidence_label(row.get('score') or 0.0)}")
    print("  Rule: nguồn bị hạ ưu tiên vẫn trace được, nhưng không tự lấn nguồn chính.")


def print_context_recovery():
    context = broker_context_snapshot()
    vision_text = _fresh_vision_text()
    policy = context_priority_policy(context=context, vision_description=vision_text)
    reconcile = vision_text_reconcile_report(context, vision_text)
    age = policy.get("age")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🧭 Context Recovery")
    print(
        "  Browser: "
        f"available={context.get('browser_available')} | kind={policy.get('kind')} | "
        f"state={policy.get('state')} | age={age_text}"
    )
    print(f"  Priority: {format_context_priority_summary(policy)}")
    print(f"  Vision/text: {reconcile.get('status')}")
    print(f"  Next: {context_recovery_next_step(context, vision_description=vision_text)}")
    print("  Rule: recovery chỉ gợi ý bước tiếp theo; không tự mở app, click, type hoặc post.")


def print_video_context():
    context = broker_context_snapshot()
    kind = ((context.get("browser_kind") or "")).lower()
    age = context.get("browser_age_seconds")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🎬 Video Context")
    print("  Action: read-only; dùng snapshot/cache hiện có, không gọi model.")
    print(
        "  Browser: "
        f"available={context.get('browser_available')} | kind={kind or 'unknown'} | "
        f"state={context.get('browser_snapshot_state') or 'INVALID'} | age={age_text}"
    )
    print(f"  Title: {shorten_line(context.get('browser_title'), 160) or 'none'}")
    print(f"  URL: {shorten_line(context.get('browser_url'), 160) or 'none'}")
    print(f"  Heading: {shorten_line(context.get('browser_heading'), 160) or 'none'}")
    print(f"  Local summary: {shorten_line(context.get('browser_local_summary'), 180) or 'none'}")
    print(
        "  Reconcile: "
        f"status={RUNTIME_RECONCILE.get('status')} | "
        f"last={_format_ms(RUNTIME_RECONCILE.get('last_ms'))} | "
        f"summary={shorten_line(RUNTIME_RECONCILE.get('last_summary'), 120) or 'none'}"
    )
    if kind not in {"youtube", "video", "music"}:
        print("  Status: non_video_context")
        print("  Next: mở video/YouTube rồi /br, hoặc dùng /context-confidence cho trang hiện tại.")
        return
    if context.get("browser_local_summary"):
        print("  Status: usable_cached_context")
        print("  Next: dùng title/summary này làm bối cảnh nhẹ; nếu cần sâu hơn thì /reconcile.")
    elif RUNTIME_RECONCILE.get("status") in {"queued", "running"}:
        print("  Status: reconcile_running")
        print("  Next: đợi /reconcile-status hoặc chạy lại /video-context.")
    else:
        print("  Status: light_only")
        print("  Next: /reconcile để tạo local summary nền; /reconcile-status để xem kết quả.")


def print_dom_state():
    browser = dict(context_snapshot().get("browser", {}))
    print("🌐 DOM")
    print(f"  Title: {browser.get('title')}")
    print(f"  URL: {browser.get('url')}")
    print(f"  Heading: {browser.get('page_heading')}")
    print(f"  Meta: {browser.get('meta_description')}")
    print(f"  Selected: {browser.get('selected_text')}")
    print(f"  Local summary: {browser.get('local_summary')}")
    print(f"  Social post: {browser.get('social_post_text')}")
    print(f"  Social vibe: {browser.get('social_vibe')}")
    print(f"  Local helper: {browser.get('local_helper_debug')}")
    print(f"  Site signals: {browser.get('site_signals')}")
    print(f"  DOM debug: {browser.get('dom_debug')}")


def print_action_registry():
    print("🛡️ Action Registry")
    for action in action_registry.list_actions():
        print(
            "  "
            f"{action.name} | permission={action.permission.value} | "
            f"cost={action.cost_estimate} | rollback={action.rollback}"
        )
        if action.description:
            print(f"    {action.description}")


def print_evidence_trace():
    context = broker_context_snapshot()
    policy = context_priority_policy(context=context, vision_description=_fresh_vision_text())
    age = policy.get("age")
    age_text = "None" if age is None else f"{age:.1f}s"
    print("🧾 Evidence Trace")
    print(
        "  Context: "
        f"kind={policy.get('kind')} | media={policy.get('media_mode')} | "
        f"state={policy.get('state')} | fresh={policy.get('fresh')} | "
        f"age={age_text} | priority={format_context_priority_summary(policy)}"
    )
    print(f"  Policy: {policy.get('reason')}")
    print("  Ranking: policy-first; score là độ tin từng nguồn, không phải thứ tự sort tuyệt đối.")
    rows = {row.get("name"): row for row in policy.get("rows", [])}
    order = [row.get("name") for row in (policy.get("ranked") or [])] + [
        row.get("name") for row in (policy.get("ignored") or [])
    ]
    seen = set()
    for name in order:
        if not name or name in seen:
            continue
        seen.add(name)
        row = rows.get(name)
        if not row:
            continue
        sample = shorten_line(row.get("sample"), 90) if row.get("sample") else ""
        available = "used" if row.get("available") else "missing"
        print(
            "  "
            f"{name}: {available} | score={float(row.get('score') or 0.0):.2f} "
            f"({row.get('label')})"
        )
        if sample:
            print(f"    sample: {sample}")
    print("  Rule: trace ghi cả nguồn bị bỏ qua để debug vì sao Nana tin/không tin một tín hiệu.")


def _parse_reconcile_expect(raw_text):
    text = str(raw_text or "").strip()
    if "||" in text:
        left, vision = text.split("||", 1)
    else:
        left, vision = text, ""
    if "|" in left:
        expected, sample = left.split("|", 1)
        return expected.strip(), sample.strip(), vision.strip()
    parts = left.split(maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip(), vision.strip()
    return "", "", vision.strip()


def print_reconcile_expect(raw_text):
    expected, sample, vision = _parse_reconcile_expect(raw_text)
    print("🧪 Reconcile Expect")
    print("  Action: read-only; không gọi model, không tạo draft.")
    if not expected or not sample or "<" in expected or "<" in sample or "<" in vision:
        print("  Missing: /reconcile-expect <status> | <text> || <vision description>")
        print("  Example: /reconcile-expect aligned | Honkai Star Rail lofi remix || ảnh anime cô gái")
        return
    context = {
        "browser_kind": "test",
        "browser_title": sample,
        "browser_heading": "",
        "browser_local_summary": "",
        "browser_social_post_text": "",
    }
    report = vision_text_reconcile_report(context=context, vision_description=vision)
    status = "pass" if report["status"] == expected else "fail"
    print(f"  Status: {status}")
    print(f"  Expected: {expected}")
    print(f"  Got: {report['status']}")
    print(f"  Overlap: {float(report.get('overlap') or 0.0):.2f}")
    print(f"  Text groups: {', '.join(report['text_groups']) if report['text_groups'] else 'none'}")
    print(f"  Vision groups: {', '.join(report['vision_groups']) if report['vision_groups'] else 'none'}")
    print(f"  Shared tokens: {', '.join(report.get('shared') or []) if report.get('shared') else 'none'}")
    if status == "fail":
        print("  Fail stage: reconcile")
        print(f"  Fail reason: expected={expected}")
    print(f"  Text: {shorten_line(sample, 160)}")
    print(f"  Vision: {shorten_line(vision, 160) if vision else 'none'}")


def _reconcile_guard_matrix_rows():
    examples = [
        ("aligned", "First Starship V3 launch later this week", "tên lửa đang bay lên với khói trắng"),
        ("aligned", "Cyrene Theme Music 1 HOUR tnbee mix Honkai Star Rail", "ảnh minh họa anime cô gái tóc hồng"),
        ("missing_vision", "Cyrene Theme Music 1 HOUR tnbee mix Honkai Star Rail", ""),
        ("conflict", "video xe máy tông cột điện trong hẻm", "ảnh minh họa anime cô gái tóc hồng"),
    ]
    rows = []
    for expected, source, vision in examples:
        context = {
            "browser_kind": "test",
            "browser_title": source,
            "browser_heading": "",
            "browser_local_summary": "",
            "browser_social_post_text": "",
        }
        report = vision_text_reconcile_report(context=context, vision_description=vision)
        status = "pass" if report["status"] == expected else "fail"
        rows.append({
            "expected": expected,
            "got": report["status"],
            "status": status,
            "text_groups": report["text_groups"],
            "vision_groups": report["vision_groups"],
            "source": source,
            "vision": vision,
        })
    return rows


def print_reconcile_guard_status():
    rows = _reconcile_guard_matrix_rows()
    pass_count = sum(1 for row in rows if row["status"] == "pass")
    print("🧩 Reconcile Guard Status")
    print("  Action: read-only; không gọi model, không tạo draft.")
    print(f"  Summary: {pass_count}/{len(rows)} pass")
    for row in rows:
        print(
            "  "
            f"{row['expected']} | {row['status']} | got={row['got']} | "
            f"text={','.join(row['text_groups']) or 'none'} | "
            f"vision={','.join(row['vision_groups']) or 'none'}"
        )
        if row["status"] != "pass":
            print(f"    source: {shorten_line(row['source'], 140)}")
            print(f"    vision: {shorten_line(row['vision'], 140) if row['vision'] else 'none'}")


def print_reconcile_check():
    context = broker_context_snapshot()
    vision_text = _fresh_vision_text()
    vision_status = "fresh" if vision_text else "none"
    report = vision_text_reconcile_report(context=context, vision_description=vision_text)
    print("🧩 Vision/Text Reconcile")
    print("  Action: read-only; không gọi model, không tạo draft.")
    print(
        "  Browser: "
        f"kind={context.get('browser_kind')} | title={shorten_line(context.get('browser_title'), 90)}"
    )
    print(f"  Vision: {vision_status}")
    print(f"  Status: {report['status']}")
    print(f"  Overlap: {float(report.get('overlap') or 0.0):.2f}")
    print(f"  Text groups: {', '.join(report['text_groups']) if report['text_groups'] else 'none'}")
    print(f"  Vision groups: {', '.join(report['vision_groups']) if report['vision_groups'] else 'none'}")
    print(f"  Shared tokens: {', '.join(report.get('shared') or []) if report.get('shared') else 'none'}")
    print(f"  Note: {report.get('note')}")
    print("  Rule: khi conflict/weak, vision không được tự phủ quyết post/title.")


def print_refine_auto_test(raw_text):
    from nana.brain.refiner import auto_refine_nana_reply

    result = auto_refine_nana_reply(raw_text, context=broker_context_snapshot())
    print("🧬 Refine Auto Test")
    print(f"  Status: {result.status}")
    print(f"  Guard should refine: {result.guard.should_refine}")
    print(f"  Guard score: {result.guard.score}")
    print(f"  Guard reasons: {', '.join(result.guard.reasons) if result.guard.reasons else 'none'}")
    if result.refine_result:
        print(f"  Refiner model: {result.refine_result.model}")
        print(f"  Refiner debug: {result.refine_result.debug}")
        print(f"  Refiner privacy risk: {result.refine_result.privacy_risk}")
        if result.refine_result.blocked_reasons:
            print(f"  Refiner blocked by: {', '.join(result.refine_result.blocked_reasons)}")
    print(f"  Original: {raw_text}")
    print(f"  Final: {result.final_text}")


def print_refine_guard_test(raw_text):
    from nana.brain.refiner import should_refine_reply

    decision = should_refine_reply(raw_text, context=broker_context_snapshot())
    print("🧪 Refine Guard")
    print(f"  Should refine: {decision.should_refine}")
    print(f"  Score: {decision.score}")
    print(f"  Reasons: {', '.join(decision.reasons) if decision.reasons else 'none'}")
    print(f"  Text: {raw_text}")


def print_refine_test(raw_text):
    from nana.brain.refiner import refine_nana_reply

    result = refine_nana_reply(raw_text, context=broker_context_snapshot())
    print("💎 Refine Test")
    print(f"  Status: {result.status}")
    print(f"  Model: {result.model}")
    print(f"  Debug: {result.debug}")
    print(f"  Privacy risk: {result.privacy_risk}")
    print(f"  Context allowed: {result.context_allowed}")
    if result.blocked_reasons:
        print(f"  Blocked by: {', '.join(result.blocked_reasons)}")
    print(f"  Safety: {', '.join(result.safety)}")
    print(f"  Original: {result.original}")
    print(f"  Refined: {result.refined or 'None'}")


def print_speech_shape_test(raw_text=""):
    from nana.brain.gpt import shape_chat_reply

    sample = raw_text or "reply=Chào Ba! 😊 Có gì cần hỏi không? | user=Chào Nana, nói ngắn thôi nha."
    user_text = ""
    reply_text = sample
    if "|" in sample:
        parts = [part.strip() for part in sample.split("|", 1)]
        reply_text, user_text = parts[0], parts[1]
    if reply_text.lower().startswith("reply="):
        reply_text = reply_text.split("=", 1)[1].strip()
    if user_text.lower().startswith("user="):
        user_text = user_text.split("=", 1)[1].strip()
    shaped = shape_chat_reply(reply_text, user_text=user_text)
    print("🧪 Speech Shape Test")
    print("  Action: read-only; không gọi model/voice/VTube.")
    print(f"  User: {user_text or 'none'}")
    print(f"  Input: {reply_text}")
    print(f"  Output: {shaped}")


def print_route_test(raw_text):
    from nana.brain.model_router import route_sidecar_task

    route = route_sidecar_task(raw_text, context=broker_context_snapshot())
    print("🧭 Model Router")
    _print_route(route)
    print(f"  Sanitized input: {route.sanitized_input or 'None'}")


def print_llmgate_test(raw_text):
    from nana.brain.model_router import route_sidecar_task

    context = broker_context_snapshot()
    route = route_sidecar_task(raw_text, context=context)
    context_preview = build_context_budget_preview(context)
    print("🧪 LLMGate Test")
    print(f"  Route status: {route.status}")
    print(f"  Model: {route.model}")
    print(f"  Tier: {route.tier}")
    print(f"  Privacy risk: {route.privacy_risk}")
    print(f"  External allowed: {route.external_allowed}")
    print(f"  Context allowed: {context_preview.allowed_for_external_model}")
    print(f"  Context chars: {context_preview.used_chars}/{context_preview.max_chars}")
    print(f"  Fallbacks: {', '.join(route.fallback_models) if route.fallback_models else 'none'}")
    if route.blocked_reasons:
        print(f"  Blocked by: {', '.join(route.blocked_reasons)}")
    if route.status == "blocked":
        print("  Execute: skipped")
        print(f"  Sanitized input: {route.sanitized_input or 'None'}")
        return
    if not context_preview.allowed_for_external_model:
        print("  Execute: skipped")
        print(f"  Blocked by context: {', '.join(context_preview.blocked_reasons)}")
        return

    prompt = _build_sidecar_prompt(route.sanitized_input, context_preview.packet)
    response, debug, used_model = _call_with_fallbacks([route.model] + list(route.fallback_models), prompt)
    print(f"  Used model: {used_model}")
    print(f"  Helper debug: {debug}")
    print(f"  Response: {response or 'None'}")


def print_intent_test(raw_text):
    plan = plan_intent(raw_text, context=broker_context_snapshot())
    print("🧠 Intent Plan")
    print(f"  Status: {plan.status}")
    print(f"  Intent: {plan.intent}")
    print(f"  Risk: {plan.risk}")
    print(f"  Policy: {plan.policy}")
    print(f"  Needs confirm: {plan.needs_confirm}")
    print(f"  Reason: {plan.reason}")
    print(f"  Plan: {plan.plan}")
    print(f"  Actions: {', '.join(plan.actions) if plan.actions else 'none'}")
    print(f"  Privacy risk: {plan.privacy_risk}")
    if plan.blocked_reasons:
        print(f"  Blocked by: {', '.join(plan.blocked_reasons)}")
    print(f"  Safety: {', '.join(plan.safety)}")
    print(f"  Sanitized text: {plan.sanitized_text or 'None'}")


def _print_route(route):
    print(f"  Status: {route.status}")
    print(f"  Model: {route.model}")
    print(f"  Tier: {route.tier}")
    print(f"  Intended share: {route.intended_share}")
    print(f"  Use case: {route.use_case}")
    print(f"  Reason: {route.reason}")
    print(f"  Privacy risk: {route.privacy_risk}")
    print(f"  External allowed: {route.external_allowed}")
    print(f"  Fallbacks: {', '.join(route.fallback_models) if route.fallback_models else 'none'}")
    if route.blocked_reasons:
        print(f"  Blocked by: {', '.join(route.blocked_reasons)}")
    print(f"  Safety: {', '.join(route.safety)}")


def _format_findings(findings):
    if not findings:
        return "none"
    return ", ".join(f"{finding.kind} x{finding.count} ({finding.severity})" for finding in findings)


def _confidence_label(value):
    value = float(value or 0.0)
    if value >= 0.8:
        return "high"
    if value >= 0.5:
        return "medium"
    if value > 0:
        return "low"
    return "none"


def _fresh_vision_text(max_age_seconds=180):
    if not LAST_VISION_DESCRIPTION:
        return None
    age = LAST_VISION_DESCRIPTION.get("age")
    if age is None and LAST_VISION_DESCRIPTION.get("time"):
        try:
            age = max(0.0, time.time() - float(LAST_VISION_DESCRIPTION.get("time") or 0.0))
        except (TypeError, ValueError):
            age = None
    if age is None:
        return LAST_VISION_DESCRIPTION.get("text")
    try:
        if float(age) > max_age_seconds:
            return None
    except (TypeError, ValueError):
        pass
    return LAST_VISION_DESCRIPTION.get("text")


def _format_ms(value):
    if value is None:
        return "None"
    try:
        return f"{float(value):.1f}ms"
    except (TypeError, ValueError):
        return str(value)


def _build_sidecar_prompt(raw_text, context_packet):
    return (
        "You are Nana's small diagnostic sidecar. Answer briefly in Vietnamese.\n"
        "Do not claim you can click, type, send, or control apps.\n\n"
        f"Context packet:\n{context_packet or '(none)'}\n\n"
        f"User text:\n{raw_text or ''}\n"
    )


def _call_with_fallbacks(models, prompt):
    from nana.llm import call_llmgate

    last_debug = None
    for model in models:
        response, debug = call_llmgate(model, prompt, max_tokens=180, temperature=0.2)
        last_debug = debug
        if response:
            return response, debug, model
    return "", last_debug, None
