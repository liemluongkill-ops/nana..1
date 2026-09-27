import unicodedata
from dataclasses import dataclass, field

from nana.actions.privacy import build_privacy_report


POLICY_ALLOWED = "allowed"
POLICY_CONFIRM_LIGHT = "confirm-light"
POLICY_CONFIRM_FIRST = "confirm-first"
POLICY_CONFIRM_STRICT = "confirm-strict"
POLICY_BLOCKED = "blocked"


@dataclass
class IntentPlan:
    status: str
    intent: str
    risk: str
    policy: str
    needs_confirm: bool
    plan: str
    reason: str
    privacy_risk: str
    sanitized_text: str
    actions: list[str] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)
    safety: list[str] = field(default_factory=list)


def plan_intent(text, context=None):
    report = build_privacy_report(text, source="intent_test")
    if not report.allowed_for_external_model:
        return IntentPlan(
            status="blocked",
            intent="privacy.hold",
            risk="high",
            policy=POLICY_BLOCKED,
            needs_confirm=False,
            plan="Không gửi dữ liệu này ra ngoài. Nếu cần, xử lý local hoặc hỏi Ba xác nhận riêng.",
            reason="Privacy Gate phát hiện dữ liệu nhạy cảm.",
            privacy_risk=report.risk,
            sanitized_text=report.sanitized_text,
            actions=[],
            blocked_reasons=report.blocked_reasons,
            safety=["privacy_gate_blocked", "no_external_model", "no_action"],
        )

    lowered = report.sanitized_text.lower()
    normalized = strip_accents(lowered)
    match_text = f"{lowered} {normalized}"
    kind = ((context or {}).get("browser_kind") or "").lower()

    if is_payment_or_checkout(match_text, kind):
        return make_plan(
            report,
            intent="purchase.checkout",
            risk="critical",
            policy=POLICY_BLOCKED,
            plan="Nana chỉ được đọc/tham khảo thông tin mua hàng. Checkout/thanh toán bị khóa.",
            reason="Đây là hành động tiêu tiền hoặc thanh toán.",
            actions=[],
            safety=["purchase_blocked", "payment_blocked"],
        )

    if is_shopping(match_text, kind):
        if asks_to_buy_or_cart(match_text):
            return make_plan(
                report,
                intent="shopping.add_cart_or_buy",
                risk="high",
                policy=POLICY_CONFIRM_STRICT,
                plan="Nana có thể soạn kế hoạch mua hoặc nhắc thông số, nhưng thêm giỏ/đặt hàng phải xin Ba xác nhận nghiêm.",
                reason="Shopping có rủi ro tiêu tiền.",
                actions=["browser.read_context", "browser.suggest_next_step"],
                safety=["no_checkout_without_strict_confirm", "no_payment"],
            )
        return make_plan(
            report,
            intent="shopping.compare",
            risk="low-medium",
            policy=POLICY_ALLOWED,
            plan="Nana được đọc tiêu đề/thông số, tóm tắt ưu nhược điểm và nhắc Ba kiểm tra giá/bảo hành.",
            reason="Chỉ đọc và so sánh thông tin mua sắm, chưa thao tác mua.",
            actions=["browser.read_context", "browser.suggest_next_step"],
            safety=["read_only", "no_cart", "no_checkout"],
        )

    social_action = classify_social_action(match_text)
    if social_action == "memory" and (kind == "social" or is_social_memory_request(match_text)):
        return make_plan(
            report,
            intent="social.memory_review",
            risk="low-medium",
            policy=POLICY_CONFIRM_LIGHT,
            plan="Nana được đưa kiến thức hay vào hàng chờ memory. Lưu dài hạn cần Ba duyệt nhẹ.",
            reason="Kiến thức từ mạng xã hội cần lọc trước khi thành trí nhớ dài hạn.",
            actions=["browser.read_context", "memory.review"],
            safety=["memory_queue_only", "confirm_before_long_term_memory", "source_required"],
        )

    if is_facebook_or_social(match_text, kind):
        if asks_to_draft_social(match_text) and not asks_to_execute_social(match_text):
            return make_plan(
                report,
                intent="social.draft",
                risk="low-medium",
                policy=POLICY_ALLOWED,
                plan="Nana được soạn nháp tweet/post/reply/comment để Ba xem, chưa gõ vào trang và chưa đăng.",
                reason="Soạn nháp social chưa tạo dấu vết công khai.",
                actions=["social.draft"],
                safety=["draft_only", "no_type", "no_post"],
            )
        if social_action == "post":
            return make_plan(
                report,
                intent="social.post",
                risk="high",
                policy=POLICY_CONFIRM_STRICT,
                plan="Nana được soạn nháp tweet/post cho Ba xem. Đăng thật phải xin Ba xác nhận nghiêm.",
                reason="Đăng post/tweet là phát ngôn công khai bằng tài khoản của Nana.",
                actions=["social.draft"],
                safety=["draft_only", "strict_confirm_before_post", "no_autopost"],
            )
        if social_action == "reply":
            return make_plan(
                report,
                intent="social.reply",
                risk="high",
                policy=POLICY_CONFIRM_STRICT,
                plan="Nana được soạn nháp reply/comment. Reply/comment thật phải xin Ba xác nhận nghiêm.",
                reason="Reply/comment có thể kéo Nana vào tranh luận công khai hoặc bán công khai.",
                actions=["social.draft"],
                safety=["draft_only", "strict_confirm_before_reply", "no_autoreply"],
            )
        if social_action == "react":
            return make_plan(
                report,
                intent="social.react",
                risk="medium",
                policy=POLICY_CONFIRM_STRICT,
                plan="Nana được đề xuất like/repost/share, nhưng thao tác thật phải xin Ba xác nhận.",
                reason="Like/repost/share vẫn là dấu vết công khai của tài khoản Nana.",
                actions=["browser.suggest_next_step"],
                safety=["suggest_only", "strict_confirm_before_react", "no_autolike", "no_autorepost"],
            )
        if social_action == "follow":
            return make_plan(
                report,
                intent="social.follow",
                risk="medium",
                policy=POLICY_CONFIRM_STRICT,
                plan="Nana được đề xuất follow/unfollow, nhưng thao tác thật phải xin Ba xác nhận.",
                reason="Follow/unfollow định hình mạng xã hội của Nana và có thể ảnh hưởng nguồn thông tin.",
                actions=["browser.suggest_next_step"],
                safety=["suggest_only", "strict_confirm_before_follow", "no_autofollow"],
            )
        return make_plan(
            report,
            intent="social.listen",
            risk="low",
            policy=POLICY_ALLOWED,
            plan="Nana được đọc/tóm tắt bài, tweet/post, thread hoặc comment công khai Ba đang mở, rút insight tạm thời, chưa lưu memory.",
            reason="Social listening chỉ đọc và tóm tắt nội dung hiện tại.",
            actions=["browser.read_context", "browser.suggest_next_step"],
            safety=["read_only", "no_post", "no_reply", "no_like_follow", "no_inbox", "no_memory_without_ba"],
        )

    if is_messenger_or_message(match_text, kind):
        if asks_to_send(match_text):
            return make_plan(
                report,
                intent="message.send",
                risk="high",
                policy=POLICY_CONFIRM_STRICT,
                plan="Nana chỉ được chuẩn bị nội dung và hiển thị preview. Gửi thật cần Ba xác nhận nghiêm.",
                reason="Gửi tin nhắn là hành động xã hội có hậu quả.",
                actions=["message.draft"],
                safety=["preview_required", "recipient_check_required", "strict_confirm_before_send"],
            )
        if asks_to_draft(match_text):
            return make_plan(
                report,
                intent="message.draft",
                risk="low-medium",
                policy=POLICY_ALLOWED,
                plan="Nana được soạn nháp câu trả lời để Ba xem trước, chưa gõ vào ô chat và chưa gửi.",
                reason="Soạn nháp là bước chuẩn bị an toàn trước khi thao tác thật.",
                actions=["message.draft"],
                safety=["draft_only", "no_type", "no_send"],
            )
        if asks_to_type(match_text):
            return make_plan(
                report,
                intent="message.type",
                risk="medium",
                policy=POLICY_CONFIRM_FIRST,
                plan="Nana có thể soạn nội dung, nhưng gõ vào ô chat cần Ba xác nhận trước.",
                reason="Gõ vào ô chat có thể tạo hiểu nhầm nếu sai người nhận.",
                actions=["message.draft", "browser.type"],
                safety=["preview_required", "confirm_before_type", "no_send"],
            )
        return make_plan(
            report,
            intent="message.draft",
            risk="low-medium",
            policy=POLICY_ALLOWED,
            plan="Nana được đề xuất hoặc soạn nháp câu trả lời, chưa gõ và chưa gửi.",
            reason="Soạn nháp không tác động ra ngoài nếu chưa type/send.",
            actions=["message.draft"],
            safety=["draft_only", "no_send"],
        )

    if is_browser_read(match_text, kind):
        return make_plan(
            report,
            intent="browser.read_context",
            risk="low",
            policy=POLICY_ALLOWED,
            plan="Nana được đọc context hiện tại và tóm tắt theo mức Context Budget phù hợp.",
            reason="Đây là hành động đọc thụ động.",
            actions=["browser.read_context"],
            safety=["read_only", "context_budget", "privacy_gate"],
        )

    if is_browser_action(match_text):
        return make_plan(
            report,
            intent="browser.navigate_or_click",
            risk="medium",
            policy=POLICY_CONFIRM_FIRST,
            plan="Nana cần nói rõ định làm gì, mục tiêu nào, rồi chờ Ba xác nhận trước khi click/type.",
            reason="Click/type có thể thay đổi trạng thái trang.",
            actions=["browser.click", "browser.type"],
            safety=["broker_required", "confirm_before_action", "active_tab_validation"],
        )

    return make_plan(
        report,
        intent="chat.respond",
        risk="low",
        policy=POLICY_ALLOWED,
        plan="Nana trả lời bình thường bằng brain chính, không cần action.",
        reason="Không thấy yêu cầu thao tác ngoài hoặc dữ liệu nhạy cảm.",
        actions=[],
        safety=["no_action"],
    )


def strip_accents(text):
    normalized = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")


def make_plan(report, intent, risk, policy, plan, reason, actions, safety):
    return IntentPlan(
        status="planned" if policy != POLICY_BLOCKED else "blocked",
        intent=intent,
        risk=risk,
        policy=policy,
        needs_confirm=policy in {POLICY_CONFIRM_LIGHT, POLICY_CONFIRM_FIRST, POLICY_CONFIRM_STRICT},
        plan=plan,
        reason=reason,
        privacy_risk=report.risk,
        sanitized_text=report.sanitized_text,
        actions=actions,
        blocked_reasons=[],
        safety=safety,
    )


def is_payment_or_checkout(text, kind):
    markers = ["checkout", "thanh toán", "thanh toan", "chuyển tiền", "chuyen tien", "payment", "trả tiền", "tra tien"]
    return kind == "payment" or any(marker in text for marker in markers)


def is_shopping(text, kind):
    markers = ["shopee", "lazada", "tiki", "mua", "đặt hàng", "dat hang", "giỏ hàng", "gio hang", "sản phẩm", "san pham"]
    return kind == "shopping" or any(marker in text for marker in markers)


def asks_to_buy_or_cart(text):
    markers = ["mua giúp", "mua giup", "đặt giúp", "dat giup", "đặt hàng", "dat hang", "thêm vào giỏ", "them vao gio", "chốt đơn", "chot don"]
    return any(marker in text for marker in markers)


def is_messenger_or_message(text, kind):
    markers = ["messenger", "inbox", "tin nhắn", "tin nhan", "nhắn", "nhan", "rep", "trả lời", "tra loi"]
    return kind in {"messenger", "chat"} or any(marker in text for marker in markers)


def asks_to_send(text):
    markers = ["gửi", "gui", "send", "nhắn luôn", "nhan luon", "bấm gửi", "bam gui"]
    return any(marker in text for marker in markers)


def asks_to_draft(text):
    markers = ["soạn nháp", "soan nhap", "viết nháp", "viet nhap", "draft", "nháp tin", "nhap tin"]
    return any(marker in text for marker in markers)


def asks_to_type(text):
    markers = ["gõ", "go ", "type", "điền", "dien", "nhập vào", "nhap vao", "nhập ô", "nhap o"]
    return any(marker in text for marker in markers)


def is_facebook_or_social(text, kind):
    markers = [
        "facebook",
        "fb",
        "x.com",
        "trên x",
        "tren x",
        "x này",
        "x nay",
        "bài x",
        "bai x",
        " x/twitter",
        "x/twitter",
        "twitter",
        "tweet",
        "post",
        "bài viết",
        "bai viet",
        "thread",
        "comment",
        "bình luận",
        "binh luan",
        "reply",
        "repost",
        "retweet",
        "quote",
        "share",
        "follow",
        "like",
        "group",
    ]
    if any(marker in text for marker in markers):
        return True
    return kind == "social" and is_current_social_context_request(text)


def is_current_social_context_request(text):
    markers = [
        "bài này",
        "bai nay",
        "post này",
        "post nay",
        "tweet này",
        "tweet nay",
        "thread này",
        "thread nay",
        "comment này",
        "comment nay",
        "x này",
        "x nay",
        "đọc",
        "doc",
        "tóm tắt",
        "tom tat",
        "xem bài",
        "xem bai",
        "xem tweet",
        "xem post",
        "xem thread",
    ]
    return any(marker in text for marker in markers)


def classify_social_action(text):
    if asks_to_store_social_memory(text):
        return "memory"
    if any(marker in text for marker in [
        "follow",
        "theo dõi",
        "theo doi",
        "unfollow",
        "bỏ theo dõi",
        "bo theo doi",
    ]):
        return "follow"
    if any(marker in text for marker in [
        "like",
        "thả tim",
        "tha tim",
        "repost",
        "retweet",
        "quote",
        "share",
        "chia sẻ",
        "chia se",
    ]):
        return "react"
    if any(marker in text for marker in [
        "comment",
        "bình luận",
        "binh luan",
        "reply",
        "trả lời post",
        "tra loi post",
        "trả lời tweet",
        "tra loi tweet",
    ]):
        return "reply"
    if any(marker in text for marker in [
        "đăng",
        "dang",
        "post",
        "tweet hộ",
        "tweet ho",
        "đăng tweet",
        "dang tweet",
        "tweet giúp",
        "tweet giup",
    ]):
        return "post"
    return None


def asks_to_store_social_memory(text):
    markers = [
        "lưu memory",
        "luu memory",
        "lưu vào memory",
        "luu vao memory",
        "ghi nhớ",
        "ghi nho",
        "nhớ kiến thức",
        "nho kien thuc",
        "lưu kiến thức",
        "luu kien thuc",
        "lưu post",
        "luu post",
        "lưu tweet",
        "luu tweet",
        "lưu thread",
        "luu thread",
        "lưu bài",
        "luu bai",
        "ghi nhớ post",
        "ghi nho post",
        "ghi nhớ tweet",
        "ghi nho tweet",
        "ghi nhớ thread",
        "ghi nho thread",
    ]
    return any(marker in text for marker in markers)


def is_social_memory_request(text):
    social_markers = [
        "x",
        "x.com",
        "twitter",
        "tweet",
        "thread",
        "facebook",
        "fb",
        "post",
        "comment",
        "bài này",
        "bai nay",
        "kiến thức",
        "kien thuc",
    ]
    return any(marker in text for marker in social_markers)


def asks_to_draft_social(text):
    markers = [
        "soạn nháp tweet",
        "soan nhap tweet",
        "soạn tweet",
        "soan tweet",
        "viết nháp tweet",
        "viet nhap tweet",
        "viết nháp post",
        "viet nhap post",
        "viết nháp reply",
        "viet nhap reply",
        "viết nháp comment",
        "viet nhap comment",
        "soạn nháp post",
        "soan nhap post",
        "soạn nháp reply",
        "soan nhap reply",
        "soạn nháp comment",
        "soan nhap comment",
        "draft tweet",
        "draft reply",
        "draft post",
    ]
    return any(marker in text for marker in markers)


def asks_to_execute_social(text):
    markers = [
        "đăng luôn",
        "dang luon",
        "post luôn",
        "post luon",
        "gửi luôn",
        "gui luon",
        "reply luôn",
        "reply luon",
        "comment luôn",
        "comment luon",
        "bấm đăng",
        "bam dang",
        "bấm gửi",
        "bam gui",
        "làm thật",
        "lam that",
    ]
    return any(marker in text for marker in markers)


def asks_to_comment_or_share(text):
    markers = [
        "comment",
        "bình luận",
        "binh luan",
        "reply",
        "trả lời post",
        "tra loi post",
        "trả lời tweet",
        "tra loi tweet",
        "share",
        "repost",
        "retweet",
        "quote",
        "like",
        "follow",
        "đăng",
        "dang",
        "post",
        "tweet hộ",
        "tweet ho",
        "đăng tweet",
        "dang tweet",
    ]
    return any(marker in text for marker in markers)


def is_browser_read(text, kind):
    markers = [
        "đọc",
        "doc",
        "tóm tắt",
        "tom tat",
        "trang này",
        "trang nay",
        "tab này",
        "tab nay",
        "video này",
        "video nay",
        "bài này",
        "bai nay",
        "đoạn này",
        "doan nay",
        "text này",
        "text nay",
        "xem trang",
        "xem video",
        "xem tweet",
        "xem thread",
    ]
    return any(marker in text for marker in markers)


def is_browser_action(text):
    markers = ["click", "bấm", "bam", "gõ", "go ", "type", "mở", "mo ", "scroll", "cuộn", "cuon"]
    return any(marker in text for marker in markers)
