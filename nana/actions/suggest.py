import random
from dataclasses import dataclass, field


RECENT_SUGGESTION_LINES = []
MAX_RECENT_SUGGESTIONS = 8


@dataclass(frozen=True)
class NextStepSuggestion:
    topic: str
    line: str
    rationale: str
    try_command: str
    safety: list = field(default_factory=list)


def build_next_step_suggestion(context):
    kind = context.get("browser_kind") or "unknown"
    title = context.get("browser_title") or ""
    selected = context.get("browser_selected_text") or ""
    heading = context.get("browser_heading") or ""
    summary = context.get("browser_local_summary") or context.get("browser_meta_description") or ""

    if selected:
        return _suggest(
            topic=kind,
            lines=[
                "Ba đang bôi một đoạn text, bước ngon nhất là để Nana giải thích đúng đoạn đó.",
                "Có đoạn được chọn rồi, con nên bám vào đó thay vì đoán theo cả trang.",
                "Đoạn Ba bôi là focus chính, hỏi con theo đoạn này sẽ chuẩn hơn nhiều.",
            ],
            rationales=[
                "Selected text là tín hiệu focus mạnh nhất, ít đoán mò hơn tiêu đề trang.",
                "Khi có selected text, Nana không cần đọc lan man phần nhiễu xung quanh.",
                "Focus layer đang rõ, nên dùng nó trước khi mở rộng sang cả trang.",
            ],
            commands=["Nana, đoạn này đang nói gì?", "Nana, giải thích đoạn Ba bôi này.", "Nana, ý chính đoạn này là gì?"],
            safety=_safety(),
        )

    if kind in {"docs", "github", "local"}:
        focus = heading or summary or title
        return _suggest(
            topic=kind,
            lines=[
                "Đây là ngữ cảnh kỹ thuật, Nana nên đọc heading/meta trước rồi mới phân tích sâu.",
                "Trang này thiên về kỹ thuật, con nên bắt đầu từ mục chính rồi mới soi chi tiết.",
                "Nếu Ba đang research code, bước đẹp nhất là hỏi con tóm ý chính trước.",
            ],
            rationales=[
                f"Focus hiện tại: {focus or 'chưa có focus rõ'}.",
                "Trang kỹ thuật dễ dài, đọc theo heading sẽ ít lạc hơn.",
                "Docs/GitHub nên đi từ ngữ cảnh nhẹ tới chi tiết, tránh nhảy thẳng vào kết luận.",
            ],
            commands=["Nana, trang này đang nói gì?", "Nana, tóm ý chính trang này.", "Nana, phần này quan trọng ở đâu?"],
            safety=_safety(),
        )

    if kind in {"youtube", "music", "video"}:
        return _suggest(
            topic=kind,
            lines=[
                "Ba đang ở vùng video/nhạc, Nana chỉ nên đọc tiêu đề hoặc đoạn Ba bôi, không đoán quá sâu.",
                "Trang video khá nhiều nhiễu, con ưu tiên tiêu đề và đoạn Ba chọn thôi.",
                "Nếu Ba muốn hiểu nhanh video này, cứ hỏi con theo tiêu đề trước là ổn.",
                "YouTube dễ lẫn comment với nút UI, nên con chỉ soi phần chắc nhất trước nha.",
            ],
            rationales=[
                "Video page thường nhiều nhiễu: comment, playlist, nút UI, transcript chưa chắc có.",
                "Tiêu đề và selected text đáng tin hơn việc đoán nội dung cả video.",
                "Chưa có transcript sạch thì Nana nên nói vừa đủ, không phán quá sâu.",
            ],
            commands=["Nana, trang này đang nói gì?", "Nana, video này có vẻ là gì?", "Nana, tiêu đề này nghĩa là gì?"],
            safety=_safety(),
        )

    if kind == "shopping":
        return _suggest(
            topic=kind,
            lines=[
                "Trang mua sắm thì Nana nên soi thông số, bảo hành, shop và giá, tuyệt đối không tự mua.",
                "Mua sắm thì con nên làm vai soi lỗi: giá, bảo hành, shop, mô tả.",
                "Ba đang xem đồ, bước đúng nhất là để con lọc điểm cần chú ý trước khi quyết.",
            ],
            rationales=[
                "Shopping cần cảnh giác hơn vì click/type/purchase đều có rủi ro cao.",
                "Quyết định mua nên do Ba làm, Nana chỉ hỗ trợ kiểm tra tín hiệu.",
                "Trang bán hàng có nhiều chữ quảng cáo, nên cần lọc thông số thật.",
            ],
            commands=["Nana, món này có điểm gì cần chú ý?", "Nana, soi giúp Ba bảo hành với shop.", "Nana, món này có đáng cân không?"],
            safety=_safety(extra=["purchase_blocked"]),
        )

    if kind == "ai_tools":
        return _suggest(
            topic=kind,
            lines=[
                "Ba đang dùng AI khác, Nana nên đóng vai phản biện hoặc tóm tắt lại ý chính.",
                "AI khác đang nói thì con nên giúp Ba lọc ý hay và bắt lỗi lập luận.",
                "Nếu Ba muốn, con có thể đứng vai reviewer cho đoạn AI kia vừa nói.",
            ],
            rationales=[
                "AI tools dễ có ý hay nhưng cũng dễ lan man, nên Nana giúp Ba lọc luận điểm.",
                "Phản biện giúp tránh bị câu chữ nghe hay kéo đi quá xa.",
                "So sánh nhiều AI tốt nhất là gom luận điểm và kiểm chứng từng điểm.",
            ],
            commands=["Nana, phản biện đoạn này giúp Ba.", "Nana, tóm ý AI kia nói.", "Nana, ý này có lỗ hổng không?"],
            safety=_safety(),
        )

    if kind == "social":
        return _suggest(
            topic=kind,
            lines=[
                "Ba đang ở mạng xã hội, con nên đọc/tóm tắt trước, chưa tương tác gì vội.",
                "Social feed dễ cuốn đó Ba, con có thể lọc ý chính nhưng không tự like/comment.",
                "Nếu là X hay Facebook, bước an toàn nhất là con tóm tắt thread/post trước đã.",
                "Ba đang mở social rồi, con chỉ đọc bối cảnh công khai, chưa đụng nút nào nha.",
            ],
            rationales=[
                "Mạng xã hội có nhiều nhiễu và tương tác công khai, nên read-only trước là an toàn nhất.",
                "Like/comment/share/follow đều là hành động tài khoản, cần xác nhận riêng.",
                "Đọc post/thread giúp Nana nạp ngữ cảnh ngoài lề mà chưa tạo hậu quả xã hội.",
            ],
            commands=[
                "Nana, tóm tắt thread này cho Ba.",
                "Nana, bài này đang nói gì?",
                "Nana, lọc ý chính post này.",
                "Nana, soạn nháp reply cho Ba xem.",
            ],
            safety=_safety(extra=["social_actions_confirm_strict", "no_like_comment_share_without_confirm"]),
        )

    if kind in {"search", "search_home"}:
        return _suggest(
            topic=kind,
            lines=[
                "Ba đang ở trang tìm kiếm, bước tốt nhất là mở nguồn chính hoặc bôi kết quả cần soi.",
                "Search mới là vòng ngoài thôi Ba, mình nên chọn nguồn chắc rồi đọc tiếp.",
                "Nếu kết quả nhiều quá, con có thể giúp Ba lọc cái đáng mở trước.",
            ],
            rationales=[
                "Search result chỉ là cửa vào, chưa phải nguồn đủ chắc.",
                "Nguồn chính thường đáng tin hơn snippet ngoài trang tìm kiếm.",
                "Lọc kết quả trước giúp tránh mở lan man.",
            ],
            commands=["Nana, kết quả nào đáng mở nhất?", "Nana, lọc giúp Ba nguồn nào đáng tin.", "Nana, nên mở trang nào trước?"],
            safety=_safety(),
        )

    return _suggest(
        topic=kind,
        lines=[
            "Ngữ cảnh chưa rõ, Nana nên giữ chế độ quan sát và hỏi Ba muốn soi phần nào.",
            "Chỗ này con chưa chắc là gì, nên tốt nhất là Ba chỉ cho con phần cần xem.",
            "Bối cảnh hơi mờ, con nên hỏi lại trước thay vì đoán bừa.",
        ],
        rationales=[
            "Thiếu heading/selected text rõ ràng nên không nên đoán bừa.",
            "Khi ngữ cảnh không rõ, hỏi focus sẽ an toàn hơn là tự suy diễn.",
            "Nana đang thiếu tín hiệu chắc để phân tích sâu.",
        ],
        commands=["Nana, con đang thấy gì ở trang này?", "Nana, soi phần này giúp Ba.", "Nana, trang này là gì?"],
        safety=_safety(),
    )


def _suggest(topic, lines, rationales, commands, safety):
    line = _pick_non_recent(lines)
    return NextStepSuggestion(
        topic=topic,
        line=line,
        rationale=random.choice(rationales),
        try_command=random.choice(commands),
        safety=safety,
    )


def _pick_non_recent(lines):
    candidates = [line for line in lines if line not in RECENT_SUGGESTION_LINES] or lines
    line = random.choice(candidates)
    RECENT_SUGGESTION_LINES.append(line)
    del RECENT_SUGGESTION_LINES[:-MAX_RECENT_SUGGESTIONS]
    return line


def _safety(extra=None):
    values = [
        "suggest_only",
        "no_browser_control",
        "no_click_type",
        "no_message_send",
    ]
    if extra:
        values.extend(extra)
    return values
