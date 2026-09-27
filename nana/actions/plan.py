from dataclasses import dataclass, field


@dataclass(frozen=True)
class ActionPlan:
    action: str
    mode: str
    target_title: str
    target_url: str
    browser_kind: str
    would_do: str
    risk: str
    draft_text: str = ""
    safety: list = field(default_factory=list)
    checks: list = field(default_factory=list)


def build_action_plan(pending_action, current_context=None):
    proposed = dict(pending_action.context or {})
    current = dict(current_context or {})
    context = current or proposed

    action = pending_action.action
    target_title = context.get("browser_title") or proposed.get("browser_title") or "None"
    target_url = context.get("browser_url") or proposed.get("browser_url") or "None"
    browser_kind = context.get("browser_kind") or proposed.get("browser_kind") or "unknown"

    return ActionPlan(
        action=action,
        mode=_mode(action),
        target_title=target_title,
        target_url=target_url,
        browser_kind=browser_kind,
        would_do=_would_do(action),
        risk=_risk_level(action),
        draft_text=proposed.get("draft_text", ""),
        safety=_safety_notes(action, proposed, current),
        checks=_checks(proposed, current),
    )


def _mode(action):
    if action in {"browser.scroll", "social.type_draft"}:
        return "confirm-execute"
    return "dry-run"


def _would_do(action):
    mapping = {
        "browser.read_context": "Đọc ngữ cảnh trình duyệt hiện tại.",
        "browser.suggest_next_step": "Chỉ gợi ý bước tiếp theo, không chạm vào trình duyệt.",
        "browser.scroll": "Nếu nối executor thật, Nana sẽ cuộn Edge xuống một nhịp nhỏ.",
        "browser.click": "Hiện tại click vẫn khóa; chỉ lập kế hoạch và chờ policy mở sau.",
        "browser.type": "Hiện tại type vẫn khóa; chỉ lập kế hoạch và chờ policy mở sau.",
        "social.type_draft": "Nana sẽ nhập draft đã duyệt vào ô soạn social đang được focus; chưa bấm đăng.",
        "purchase.checkout": "Bị chặn cứng; không lập kế hoạch mua hàng.",
        "message.send": "Bị chặn cứng; không lập kế hoạch gửi tin nhắn.",
    }
    return mapping.get(action, "Action không nằm trong kế hoạch thực thi an toàn.")


def _risk_level(action):
    if action in {"browser.read_context", "browser.suggest_next_step"}:
        return "low"
    if action == "browser.scroll":
        return "low-medium"
    if action == "social.type_draft":
        return "medium-high"
    if action in {"browser.click", "browser.type"}:
        return "medium"
    return "high"


def _safety_notes(action, proposed, current):
    notes = [
        "confirm_required",
        "no_purchase_or_message_send",
    ]
    if action == "browser.scroll":
        notes.append("scroll_only_executor")
    elif action == "social.type_draft":
        notes.extend([
            "draft_approved",
            "focused_composer_only",
            "no_click",
            "no_post_send",
            "no_submit_key",
            "requires_active_social_tab",
        ])
    else:
        notes.append("no_real_browser_control")
    if action in {"browser.click", "browser.type"}:
        notes.append("requires_explicit_target_before_real_executor")
    if proposed.get("debug_force_edge"):
        notes.append("debug_force_edge_used")
    if current and not current.get("active_window_valid"):
        notes.append("current_context_not_executor_ready")
    return notes


def _checks(proposed, current):
    checks = [
        _check("proposal_browser_available", proposed.get("browser_available")),
        _check("proposal_browser_fresh", proposed.get("browser_fresh")),
        _check("proposal_edge_active", proposed.get("active_app_is_edge")),
        _check("proposal_window_valid", proposed.get("active_window_valid")),
    ]
    if current:
        checks.extend([
            _check("current_browser_available", current.get("browser_available")),
            _check("current_browser_fresh", current.get("browser_fresh")),
            _check("current_edge_active", current.get("active_app_is_edge")),
            _check("current_window_valid", current.get("active_window_valid")),
            _same_url_check(proposed.get("browser_url"), current.get("browser_url")),
        ])
    return checks


def _check(name, value):
    return f"{name}={'ok' if value else 'no'}"


def _same_url_check(proposed_url, current_url):
    if not proposed_url or not current_url:
        return "same_url=unknown"
    return f"same_url={'ok' if proposed_url == current_url else 'changed'}"
