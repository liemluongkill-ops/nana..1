from dataclasses import dataclass, field
import os

from nana.actions.privacy import build_privacy_report


CHEAP_MODEL = os.getenv("NANA_ROUTER_CHEAP_MODEL", "gpt-5.4-mini")
REASONING_MODEL = os.getenv("NANA_ROUTER_REASONING_MODEL", "gpt-5.4")
NUCLEAR_MODEL = os.getenv("NANA_ROUTER_NUCLEAR_MODEL", "gpt-5.5")
LOCAL_ONLY_MODEL = "local_only"


@dataclass
class ModelRoute:
    status: str
    model: str
    tier: str
    intended_share: str
    use_case: str
    reason: str
    privacy_risk: str
    external_allowed: bool
    sanitized_input: str
    fallback_models: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    safety: list[str] = field(default_factory=list)


def route_sidecar_task(text, context=None):
    report = build_privacy_report(text, source="route_test")
    if not report.allowed_for_external_model:
        return ModelRoute(
            status="blocked",
            model=LOCAL_ONLY_MODEL,
            tier="privacy_hold",
            intended_share="0%",
            use_case="local_only_or_ask_ba",
            reason="Privacy Gate phát hiện dữ liệu nhạy cảm cấp cao.",
            privacy_risk=report.risk,
            external_allowed=False,
            sanitized_input=report.sanitized_text,
            fallback_models=[],
            blocked_reasons=report.blocked_reasons,
            safety=[
                "no_external_model",
                "privacy_gate_blocked",
                "ask_ba_before_any_upload",
            ],
        )

    lowered = (report.sanitized_text or "").lower()
    kind = ((context or {}).get("browser_kind") or "").lower()

    if should_use_nuclear(lowered):
        return ModelRoute(
            status="routed",
            model=NUCLEAR_MODEL,
            tier="nuclear_5_percent",
            intended_share="5%",
            use_case="hard_reasoning_or_premium_refine",
            reason="Câu này có dấu hiệu cực khó/VIP/vision/refiner xịn, nên để 5.5 xử lý khi thật cần.",
            privacy_risk=report.risk,
            external_allowed=True,
            sanitized_input=report.sanitized_text,
            fallback_models=["gpt-5.4"],
            safety=[
                "privacy_gate_passed",
                "manual_trigger_only",
                "not_main_brain",
                "no_action_control",
            ],
        )

    if should_use_reasoning(lowered, kind):
        return ModelRoute(
            status="routed",
            model=REASONING_MODEL,
            tier="reasoning_15_percent",
            intended_share="15%",
            use_case="code_reasoning_review",
            reason="Câu này thiên về code/logic/debug/refactor, nên dùng model reasoning chính của Nana.",
            privacy_risk=report.risk,
            external_allowed=True,
            sanitized_input=report.sanitized_text,
            fallback_models=["gpt-5.5", "gpt-5.4-mini"],
            safety=[
                "privacy_gate_passed",
                "manual_trigger_only",
                "no_action_control",
            ],
        )

    return ModelRoute(
        status="routed",
        model=CHEAP_MODEL,
        tier="cheap_80_percent",
        intended_share="80%",
        use_case="summary_classify_reaction_light",
        reason="Việc này nhẹ: tóm tắt, phân loại, reaction, hoặc context nhỏ. Dùng model rẻ để tiết kiệm credit.",
        privacy_risk=report.risk,
        external_allowed=True,
        sanitized_input=report.sanitized_text,
        fallback_models=["gpt-5.4-mini"],
        safety=[
            "privacy_gate_passed",
            "manual_trigger_only",
            "cheap_first",
            "no_action_control",
        ],
    )


def should_use_nuclear(text):
    markers = [
        "5.5",
        "cực hạn",
        "cuc han",
        "khủng nhất",
        "khung nhat",
        "max công lực",
        "max cong luc",
        "premium",
        "refiner xịn",
        "refiner xin",
        "siêu tự nhiên",
        "sieu tu nhien",
        "uốn lưỡi",
        "uon luoi",
        "vision",
        "ảnh màn hình",
        "anh man hinh",
        "screenshot",
        "soi ảnh",
        "soi anh",
    ]
    return any(marker in text for marker in markers)


def should_use_reasoning(text, kind):
    if kind in {"github", "docs", "local"}:
        return True

    markers = [
        "bug",
        "code",
        "compile",
        "debug",
        "deadlock",
        "exception",
        "fix",
        "logic",
        "module",
        "optimize",
        "refactor",
        "review",
        "runtime",
        "stack trace",
        "traceback",
        "test",
        "tối ưu",
        "toi uu",
        "sửa lỗi",
        "sua loi",
        "phức tạp",
        "phuc tap",
    ]
    return any(marker in text for marker in markers)
