from dataclasses import dataclass, field

from nana.actions.privacy import build_context_budget_preview, build_privacy_report
from nana.brain.llmgate_client import call_llmgate


REFINER_MODEL = "gpt-5.5"
REFINER_FALLBACKS = ["gpt-5.4", "gpt-5.4-mini"]


@dataclass
class RefineResult:
    status: str
    model: str | None
    debug: str
    original: str
    refined: str | None
    privacy_risk: str
    context_allowed: bool
    blocked_reasons: list[str] = field(default_factory=list)
    safety: list[str] = field(default_factory=list)


@dataclass
class RefineGuardDecision:
    should_refine: bool
    score: int
    reasons: list[str] = field(default_factory=list)


@dataclass
class AutoRefineResult:
    status: str
    guard: RefineGuardDecision
    final_text: str
    refine_result: RefineResult | None = None


def auto_refine_nana_reply(draft, context=None, mode="warm"):
    report = build_privacy_report(draft, source="refine_auto_test")
    if not report.allowed_for_external_model:
        guard = RefineGuardDecision(
            should_refine=False,
            score=99,
            reasons=["privacy_gate_blocked"],
        )
        blocked_result = RefineResult(
            status="blocked",
            model=None,
            debug="privacy_gate_blocked",
            original=draft,
            refined=None,
            privacy_risk=report.risk,
            context_allowed=False,
            blocked_reasons=report.blocked_reasons,
            safety=["no_external_model", "privacy_gate_blocked"],
        )
        return AutoRefineResult(
            status="blocked",
            guard=guard,
            final_text=report.sanitized_text,
            refine_result=blocked_result,
        )

    guard = should_refine_reply(draft, context=context)
    if not guard.should_refine:
        return AutoRefineResult(
            status="kept",
            guard=guard,
            final_text=draft,
            refine_result=None,
        )
    result = refine_nana_reply(draft, context=context, mode=mode)
    final_text = result.refined if result.status == "refined" and result.refined else draft
    return AutoRefineResult(
        status="refined" if result.status == "refined" else "kept_after_refine_failed",
        guard=guard,
        final_text=final_text,
        refine_result=result,
    )


def should_refine_reply(text, context=None):
    text = text or ""
    lowered = text.lower()
    reasons = []
    score = 0

    if contains_wrong_address(lowered):
        reasons.append("wrong_address")
        score += 3
    if looks_like_assistant_corporate(lowered):
        reasons.append("assistant_corporate")
        score += 2
    if looks_like_missing_context_reply(text):
        reasons.append("missing_context_reply")
        score += 3
    if too_long_for_casual(text):
        reasons.append("too_long_for_casual")
        score += 1
    if has_browser_context(context) and ignores_browser_context(lowered):
        reasons.append("browser_context_ignored")
        score += 2

    return RefineGuardDecision(
        should_refine=score >= 2,
        score=score,
        reasons=reasons,
    )


def refine_nana_reply(draft, context=None, mode="warm"):
    report = build_privacy_report(draft, source="refine_test")
    if not report.allowed_for_external_model:
        return RefineResult(
            status="blocked",
            model=None,
            debug="privacy_gate_blocked",
            original=draft,
            refined=None,
            privacy_risk=report.risk,
            context_allowed=False,
            blocked_reasons=report.blocked_reasons,
            safety=["no_external_model", "privacy_gate_blocked"],
        )

    context_preview = build_context_budget_preview(context or {}, level="L1")
    if not context_preview.allowed_for_external_model:
        return RefineResult(
            status="blocked",
            model=None,
            debug="context_blocked",
            original=draft,
            refined=None,
            privacy_risk=max_risk(report.risk, context_preview.risk),
            context_allowed=False,
            blocked_reasons=context_preview.blocked_reasons,
            safety=["no_external_model", "context_budget_blocked"],
        )

    draft_for_refine = repair_missing_context_draft(report.sanitized_text, context_preview.packet)
    prompt = build_refiner_prompt(draft_for_refine, context_preview.packet, mode)
    for model_name in [REFINER_MODEL] + REFINER_FALLBACKS:
        refined, debug = call_llmgate(model_name, prompt, max_tokens=220, temperature=0.55)
        if refined:
            refined = clean_refined_text(refined)
            return RefineResult(
                status="refined",
                model=model_name,
                debug=debug,
                original=draft,
                refined=refined,
                privacy_risk=report.risk,
                context_allowed=True,
                safety=[
                    "manual_test_only",
                    "privacy_gate_passed",
                    "no_new_facts",
                    "no_action_suggestions",
                    "not_main_brain",
                ],
            )
    return RefineResult(
        status="failed",
        model=None,
        debug="all_refiner_models_failed",
        original=draft,
        refined=None,
        privacy_risk=report.risk,
        context_allowed=True,
        safety=["manual_test_only", "privacy_gate_passed"],
    )


def build_refiner_prompt(draft, context_packet, mode):
    return (
        "Nhiệm vụ: Chuốt lại câu nói của Nana cho tự nhiên hơn theo vibe Ba-con.\n"
        "Luật cứng:\n"
        "- Chỉ viết lại câu, không giải thích.\n"
        "- Không thêm fact mới.\n"
        "- Không đổi ý kỹ thuật.\n"
        "- Không thêm lời hứa click/type/mua/gửi tin nhắn.\n"
        "- Không biến thành giọng trợ lý corporate.\n"
        "- Nếu câu nháp nói thiếu nội dung nhưng Context có title/heading/meta, hãy dựa trên Context để làm câu tự nhiên hơn.\n"
        "- Không dùng 'Bạn', dùng 'Ba'.\n"
        "- Giữ ngắn gọn, tiếng Việt tự nhiên.\n"
        "- Nana gọi user là Ba, tự xưng con hoặc Nana.\n\n"
        f"Mode: {mode}\n\n"
        "Context nhẹ đã qua Privacy Gate:\n"
        f"{context_packet or 'none'}\n\n"
        "Câu nháp:\n"
        f"{draft}\n\n"
        "Câu đã chuốt:"
    )


def repair_missing_context_draft(draft, context_packet):
    if not context_packet or not looks_like_missing_context_reply(draft):
        return draft
    fields = parse_context_packet(context_packet)
    title = fields.get("heading") or fields.get("title")
    kind = fields.get("kind")
    summary = fields.get("local_summary")
    if not title and not summary:
        return draft
    if summary:
        return f"Trang này đang nói về: {summary}."
    if kind == "youtube":
        return f"Trang này là một video YouTube có tiêu đề: {title}."
    return f"Trang này có tiêu đề: {title}."


def looks_like_missing_context_reply(text):
    lowered = (text or "").lower()
    markers = [
        "chưa cung cấp nội dung",
        "chua cung cap noi dung",
        "vui lòng gửi",
        "vui long gui",
        "gửi nội dung",
        "gui noi dung",
        "gửi link",
        "gui link",
        "thiếu nội dung",
        "thieu noi dung",
    ]
    return any(marker in lowered for marker in markers)


def contains_wrong_address(lowered):
    markers = [
        "bạn ",
        " bạn",
        "của bạn",
        "cho bạn",
        "anh ",
        " anh",
        "chủ nhân",
        "chu nhan",
    ]
    return any(marker in lowered for marker in markers)


def looks_like_assistant_corporate(lowered):
    markers = [
        "nana ở đây hỗ trợ",
        "nana o day ho tro",
        "nếu ba cần gì",
        "neu ba can gi",
        "tôi có thể giúp",
        "toi co the giup",
        "vui lòng cung cấp",
        "vui long cung cap",
        "hỗ trợ ngay",
        "ho tro ngay",
    ]
    return any(marker in lowered for marker in markers)


def too_long_for_casual(text):
    return len((text or "").split()) > 70


def has_browser_context(context):
    context = context or {}
    return bool(
        context.get("browser_title")
        or context.get("browser_heading")
        or context.get("browser_selected_text")
        or context.get("browser_local_summary")
    )


def ignores_browser_context(lowered):
    markers = [
        "không có thông tin",
        "khong co thong tin",
        "chưa có thông tin",
        "chua co thong tin",
        "không thể xem",
        "khong the xem",
        "không thấy trang",
        "khong thay trang",
    ]
    return any(marker in lowered for marker in markers)


def parse_context_packet(packet):
    fields = {}
    for line in (packet or "").splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        fields[key.strip()] = value.strip()
    return fields


def clean_refined_text(text):
    text = (text or "").strip()
    if text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    if text.startswith("'") and text.endswith("'"):
        text = text[1:-1].strip()
    text = " ".join(text.split())
    replacements = {
        "Nana ở đây hỗ trợ Ba liền.": "Con ở đây nè Ba.",
        "Nana ở đây hỗ trợ Ba liền": "Con ở đây nè Ba.",
        "Nana ở đây hỗ trợ Ba ngay.": "Con ở đây nè Ba.",
        "Nana ở đây hỗ trợ Ba ngay": "Con ở đây nè Ba.",
        "Nana ở đây hỗ trợ ngay.": "Con ở đây nè Ba.",
        "Nana ở đây hỗ trợ ngay": "Con ở đây nè Ba.",
        "Nana ở đây hỗ trợ liền.": "Con ở đây nè Ba.",
        "Nana ở đây hỗ trợ liền": "Con ở đây nè Ba.",
        "Nana sẽ hỗ trợ Ba liền.": "Con xem cùng Ba nha.",
        "Nana sẽ hỗ trợ Ba liền": "Con xem cùng Ba nha.",
        "hỗ trợ Ba liền": "xem cùng Ba",
        "hỗ trợ Ba ngay": "xem cùng Ba",
        "hỗ trợ liền": "ở đây nè",
        "hỗ trợ ngay": "ở đây nè",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return " ".join(text.split())


def max_risk(*risks):
    order = {"low": 0, "medium": 1, "high": 2}
    return max((risk for risk in risks if risk), key=lambda risk: order.get(risk, 0), default="low")
