"""nana.social.guards — fallback, compact, soften, and trim for social drafts."""
from nana.social.classifier import (
    detect_public_reaction_style,
    source_match_bundle,
    strip_accents_for_match,
    source_has_accident_or_traffic_context,
    source_has_quick_reaction_context,
    danger_context_should_not_praise_reaction,
    fallback_danger_public_reply,
)


def fallback_social_draft(intent, source_text=""):
    lowered = (source_text or "").lower()
    if "bug" in lowered:
        return "Bug nhỏ nhưng đủ làm buổi debug có mùi thử thách. Nana ghi lại để lát mình soi tiếp cho gọn."
    if "nana" in lowered:
        return "Nana vẫn đang học cách nhìn, nghĩ rồi mới làm. Chậm một chút cũng được, miễn là càng ngày càng đáng tin."
    if intent == "social.reply":
        return "Con thấy ý này đáng suy nghĩ đó, để mình đọc thêm bối cảnh rồi nói tiếp cho chắc."
    return "Ba cho con thêm chút bối cảnh, con sẽ viết nháp gọn hơn."


def fallback_public_social_reply(source_text=""):
    style = detect_public_reaction_style(source_text)
    if style == "danger":
        return fallback_danger_public_reply(source_text=source_text)
    if style == "absurd_work":
        return "Cần thưởng gấp rồi."
    if style == "animal_standoff":
        return "Ủa phe nào thắng vậy?"
    if style == "conflict":
        return "Căng thật."
    if style == "crime_violence":
        return "Tình huống này nguy hiểm thật."
    if style == "harassment_boundary":
        return "Cách xử lý này không ổn chút nào."
    if style == "minor_safety":
        return "Mong em ấy ổn."
    if style == "self_harm_sensitive":
        return "Nghe rất đáng lo."
    if style == "sexual_sensitive":
        return "Nội dung này nhạy cảm thật."
    if style == "financial_scam":
        return "Cẩn thận mất tiền oan."
    if style == "legal_sensitive":
        return "Chờ thông tin chính thức vậy."
    if style == "misinfo_uncertain":
        return "Cần kiểm chứng thêm đã."
    if style == "serious_issue":
        return "Vấn đề này đáng lo thật."
    if style == "public_safety":
        return "Cảnh báo này đáng chú ý."
    if style == "politics_sensitive":
        return "Chuyện này khá nhạy cảm."
    if style == "grief_sensitive":
        return "Xin chia buồn."
    if style == "weather_funny":
        return "Tô mì cũng vất vả thật."
    if style == "awkward_danger":
        return "Tình huống vừa căng vừa khó đỡ."
    if style == "disaster_emergency":
        return "Mong mọi người an toàn."
    if style == "medical_sensitive":
        return "Nghe đáng lo thật."
    if style == "school_memory":
        return "Kỷ niệm nhớ lâu thật."
    if style == "space_launch":
        return "Đáng chờ thật."
    if style == "overloaded_ride_funny":
        return "Chở đông mà chill thật."
    if style == "cute":
        return "Cute thế."
    if style == "funny":
        return "Cười xỉu."
    if style == "hype":
        return "Đỉnh thật."
    if style == "chill":
        return "Nhìn chill thật."
    if style == "morning":
        return "Năng lượng ghê."
    return "Đứng ngoài hóng thôi."


def soften_public_social_reply(text, source_text="", intent="social.draft"):
    if intent not in {"social.reply", "social.draft"}:
        return text
    lowered_source = (source_text or "").lower()
    normalized_source = strip_accents_for_match(lowered_source)
    source_match_text = f"{lowered_source} {normalized_source}"
    public_markers = [
        "reply", "comment",
        "tweet này", "tweet nay",
        "bài này", "bai nay",
        "post này", "post nay",
    ]
    if not any(marker in source_match_text for marker in public_markers):
        return text

    lowered = (text or "").lower()
    normalized = strip_accents_for_match(lowered)
    match_text = f"{lowered} {normalized}"
    risk_markers = [
        "gái xinh", "gai xinh", "trai đẹp", "trai dep",
        "yêu nước", "yeu nuoc", "điểm cộng", "diem cong",
        "lý tưởng", "ly tuong",
    ]
    if any(marker in match_text for marker in risk_markers):
        return fallback_public_social_reply(source_text=source_text)
    if public_social_reply_seems_off_topic(match_text, source_text=source_text):
        return fallback_public_social_reply(source_text=source_text)
    return text


def public_social_reply_seems_off_topic(reply_match_text, source_text=""):
    lowered_source = (source_text or "").lower()
    normalized_source = strip_accents_for_match(lowered_source)
    source_match_text = f"{lowered_source} {normalized_source}"
    source_is_cat = any(marker in source_match_text for marker in ["mèo", "meo", "cat", "猫", "gatinho"])
    reply_is_street_flag = any(marker in reply_match_text for marker in ["phố", "pho", "cờ đỏ", "co do", "góc đường", "goc duong"])
    if source_is_cat and reply_is_street_flag:
        return True
    return False


def trim_public_social_reply(text, source_text="", intent="social.draft"):
    lowered_source = (source_text or "").lower()
    normalized_source = strip_accents_for_match(lowered_source)
    source_match_text = f"{lowered_source} {normalized_source}"
    if intent != "social.reply" and not any(marker in source_match_text for marker in ["reply", "comment"]):
        return text
    text = " ".join(str(text or "").split())
    if len(text) <= 150:
        return text
    for separator in [". ", "! ", "? "]:
        first = text.split(separator, 1)[0].strip()
        if 24 <= len(first) <= 150:
            return first + separator.strip()
    return text[:147].rstrip() + "..."


def enforce_short_public_reply(text, source_text="", intent="social.draft"):
    if intent not in {"social.reply", "social.draft"}:
        return text
    source_match_text = source_match_bundle(source_text)
    if not any(marker in source_match_text for marker in ["siêu ngắn", "sieu ngan", "dưới 8 từ", "duoi 8 tu", "reply", "comment"]):
        return text
    style = detect_public_reaction_style(source_text)
    if style and len(str(text or "").strip()) > 56:
        return fallback_public_social_reply(source_text=source_text)
    return text


def compact_public_reaction_reply(text, source_text="", intent="social.draft"):
    if intent not in {"social.reply", "social.draft"}:
        return text
    style = detect_public_reaction_style(source_text)
    if not style:
        return text
    lowered = (text or "").lower()
    normalized = strip_accents_for_match(lowered)
    reply_match_text = f"{lowered} {normalized}"
    if style == "danger":
        if danger_context_should_not_praise_reaction(source_text) and any(marker in reply_match_text for marker in [
            "phản ứng nhanh", "phan ung nhanh", "né kịp", "ne kip",
        ]):
            return fallback_public_social_reply(source_text=source_text)
        if any(marker in reply_match_text for marker in [
            "cute", "cưng", "cung", "dễ thương", "de thuong",
            "chill", "bình yên", "binh yen", "cười xỉu", "cuoi xiu",
        ]):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 48:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "animal_standoff":
        generic_markers = ["phản ứng nhanh", "phan ung nhanh", "cute", "cưng", "cung", "dễ thương", "de thuong"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 48:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "absurd_work":
        generic_markers = [
            "nhìn chill", "nhin chill", "chill", "cute", "cưng", "cung",
            "bình yên", "binh yen", "tinh thần làm việc", "tinh than lam viec",
            "không gì cản nổi", "khong gi can noi",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 36:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "conflict":
        generic_markers = ["cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "bình yên", "binh yen", "cười xỉu", "cuoi xiu"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 36:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "crime_violence":
        return fallback_public_social_reply(source_text=source_text)
    if style == "harassment_boundary":
        generic_markers = ["cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu", "đáng đời", "dang doi", "tự chịu", "tu chiu"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 56:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "minor_safety":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "đáng đời", "dang doi", "phản ứng nhanh", "phan ung nhanh",
            "đứng ngoài hóng", "dung ngoai hong",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 42:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "self_harm_sensitive":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "đáng đời", "dang doi", "tự chịu", "tu chiu",
            "kèo này", "keo nay", "đứng ngoài hóng", "dung ngoai hong",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 42:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "sexual_sensitive":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "ngon", "sexy", "soi", "xin link", "xin clip", "hóng", "hong", "đỉnh", "dinh",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 46:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "financial_scam":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "đỉnh", "dinh", "vào kèo", "vao keo", "all in", "mua đi", "mua di",
            "đầu tư đi", "dau tu di", "hóng", "hong",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 46:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "legal_sensitive":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "chắc chắn", "chac chan", "đúng tội", "dung toi", "đáng đời", "dang doi",
            "xử luôn", "xu luon", "hóng", "hong",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 48:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "misinfo_uncertain":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "chắc chắn", "chac chan", "rõ ràng", "ro rang", "tin chuẩn", "tin chuan",
            "đúng rồi", "dung roi", "hóng", "hong",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 48:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "serious_issue":
        generic_markers = ["cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu", "đứng ngoài hóng", "dung ngoai hong"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 54:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "public_safety":
        generic_markers = ["cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu", "đứng ngoài hóng", "dung ngoai hong"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 50:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "politics_sensitive":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "đứng ngoài hóng", "dung ngoai hong",
            "kèo này", "keo nay", "phe nào", "phe nao", "đỉnh thật", "dinh that",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 46:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "grief_sensitive":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "đáng đời", "dang doi", "hóng", "hong", "kèo này", "keo nay",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 42:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "weather_funny":
        generic_markers = ["nguy hiểm", "nguy hiem", "tai nạn", "tai nan", "cute", "cưng", "cung", "cười xỉu", "cuoi xiu"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 44:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "awkward_danger":
        generic_markers = ["nguy hiểm", "nguy hiem", "cute", "cưng", "cung", "cười xỉu", "cuoi xiu"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 48:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "disaster_emergency":
        generic_markers = ["cute", "cưng", "cung", "cười xỉu", "cuoi xiu", "chill", "đáng đời", "dang doi"]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 40:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "medical_sensitive":
        generic_markers = [
            "cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu",
            "đáng đời", "dang doi",
            "uống thuốc", "uong thuoc", "đi khám", "di kham",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 42:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "school_memory":
        generic_markers = [
            "nguy hiểm", "nguy hiem", "tai nạn", "tai nan", "thót tim", "thot tim",
            "cute", "cưng", "cung", "cười xỉu", "cuoi xiu", "cảnh báo", "canh bao",
        ]
        if any(marker in reply_match_text for marker in generic_markers):
            return fallback_public_social_reply(source_text=source_text)
        if len(str(text or "").strip()) > 46:
            return fallback_public_social_reply(source_text=source_text)
        return text
    if style == "space_launch":
        if len(str(text or "").strip()) > 34:
            return fallback_public_social_reply(source_text=source_text)
        return text
    prose_markers = [
        "bình yên", "binh yen", "thế giới", "the gioi", "ồn ào", "on ao",
        "năng lượng hơn hẳn", "nang luong hon han",
        "chúc bạn", "chuc ban", "chào buổi sáng", "chao buoi sang",
    ]
    if len(str(text or "").strip()) > 42:
        return fallback_public_social_reply(source_text=source_text)
    if any(marker in reply_match_text for marker in prose_markers):
        return fallback_public_social_reply(source_text=source_text)
    return text
