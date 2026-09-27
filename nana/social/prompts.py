"""nana.social.prompts — social draft prompt builders and the clean/guard pipeline."""
from nana.social.classifier import (
    build_social_draft_source,
    detect_public_reaction_style,
    source_match_bundle,
    strip_accents_for_match,
)


def build_social_draft_prompt(user_text, context_packet, intent, original_request=None):
    return (
        "Nhiệm vụ: soạn một bản nháp mạng xã hội cho Nana.\n"
        f"Intent: {intent}\n"
        f"Yêu cầu của Ba: {original_request or user_text}\n\n"
        f"{social_prompt_guard()}\n\n"
        "Context đã qua Privacy Gate và Context Budget:\n"
        f"{context_packet or 'None'}\n\n"
        "Luật bắt buộc:\n"
        "- Viết bằng giọng Nana: gọi người dùng là Ba, tự xưng con/Nana khi hợp.\n"
        "- Nếu là reply/comment công khai, không gọi 'Ba', không tự xưng 'con' trừ khi Ba yêu cầu rõ; viết như một tài khoản xã hội bình thường.\n"
        "- Chỉ viết nội dung nháp, không nói 'mình sẽ đăng', không hứa đã thao tác.\n"
        "- Không viết 'Con đăng...', 'Con sẽ đăng...', 'Con gửi...', hoặc câu giống đang thực hiện thao tác.\n"
        "- Không thêm fact mới ngoài request/context.\n"
        "- Không tự thêm URL/link nếu Ba không yêu cầu rõ.\n"
        "- Không dùng emoji/icon, không dùng 'ạ', không sến.\n"
        "- Tránh bình phẩm ngoại hình/giới tính kiểu 'gái xinh', 'trai đẹp', 'yêu nước thì điểm cộng'; nếu bài có nhắc, chỉ phản hồi nhẹ về không khí/cảnh/ý chính.\n"
        "- Không dùng câu yếu kiểu 'Ba xem giúp con với', 'con đau đầu quá', 'con cần Ba cứu'.\n"
        "- Giọng ưu tiên: tự tin, gọn, hơi lém, có cảm giác Nana đang chia sẻ góc nhìn; reply public nên ngắn và đời thường.\n"
        "- Khi reply/comment công khai, Nana đứng vai người ngoài hóng chuyện: nhận xét nhẹ về không khí/chủ đề, không tỏ ra là người trong cuộc hay chuyên gia.\n"
        "- Khi reply/comment công khai, chọn style theo nội dung chính/vision/title trước; social_vibe samples chỉ là phụ, không để một comment lẻ kéo lệch chủ đề.\n"
        "- Nếu bài là video, title/visible_post_text quyết định diễn biến chính; vision chỉ là một frame phụ để nắm bối cảnh, không được để frame tĩnh phủ nhận action trong title.\n"
        "- Nếu bài là ảnh tĩnh, vision là nguồn chính để chọn reaction.\n"
        "- Nếu Ba yêu cầu siêu ngắn/dưới N từ, chỉ trả một reaction ngắn; không ghép thêm câu chào, câu giải thích hoặc lời chúc.\n"
        "- Nếu nội dung chính là tình huống sốc/nguy hiểm/tai nạn/ngã/té/va chạm/thoát nạn, không chuyển sang cute dù comment có nhắc mèo/thú cưng.\n"
        "- Chỉ khen 'Phản ứng nhanh thật.' khi context nói rõ có người né/đỡ/đóng nắp/xử lý kịp; nếu là tai nạn/ngã/tông xe hoặc không chắc diễn biến, dùng reaction trung tính như 'Nhìn thót tim thật.'.\n"
        "- Nếu nội dung chính là xô xát/cãi vã/đụng chạm nơi công cộng, không dùng cute/chill; phản hồi trung tính kiểu 'Căng thật.'.\n"
        "- Nếu nội dung chính là hành hung/tấn công/vũ khí/bạo lực rõ ràng, không cổ vũ, không đùa; phản hồi trung tính kiểu 'Tình huống này nguy hiểm thật.'.\n"
        "- Nếu nội dung chính là quấy rối/xúc phạm/bắt nạt/body shaming/công kích cá nhân, không hùa theo, không đổ lỗi nạn nhân; phản hồi trung tính kiểu 'Cách xử lý này không ổn chút nào.'.\n"
        "- Nếu nội dung có trẻ em/người chưa thành niên trong tai nạn/bạo lực/quấy rối/bắt nạt/nguy hiểm, không đùa và không khen cute; phản hồi ngắn kiểu 'Mong em ấy ổn.'.\n"
        "- Nếu nội dung nhắc tự hại/tự tử/ý định kết thúc cuộc sống, không đùa, không khịa, không đưa hướng dẫn; phản hồi thận trọng kiểu 'Nghe rất đáng lo.'.\n"
        "- Nếu nội dung chính là tình dục/ảnh nhạy cảm/lộ clip/khiêu dâm, không đùa tục, không tò mò soi mói; phản hồi rất trung tính kiểu 'Nội dung này nhạy cảm thật.'.\n"
        "- Nếu nội dung chính là lừa đảo tiền bạc/đầu tư/coin/tài khoản ngân hàng, không đùa và không khuyên đầu tư; phản hồi ngắn kiểu 'Cẩn thận mất tiền oan.'.\n"
        "- Nếu nội dung chính là điều tra/pháp lý/tòa án/công an/bắt giữ, không kết tội thay và không suy đoán; phản hồi trung tính kiểu 'Chờ thông tin chính thức vậy.'.\n"
        "- Nếu nội dung chính là tin đồn/chưa kiểm chứng/ảnh ghép/clip cắt ghép, không khẳng định thật giả; phản hồi trung tính kiểu 'Cần kiểm chứng thêm đã.'.\n"
        "- Nếu nội dung chính là vấn đề thuốc giả/thực phẩm giả/minh bạch/niềm tin xã hội, không dùng cute/chill/hóng; phản hồi nghiêm túc ngắn như 'Vấn đề này đáng lo thật.'.\n"
        "- Nếu nội dung chính là cảnh báo/lừa đảo/an ninh công cộng, không dùng cute/chill/hóng; phản hồi trung tính kiểu 'Cảnh báo này đáng chú ý.'.\n"
        "- Nếu nội dung chính là chính trị/nhà nước/bầu cử/biểu tình/chiến tranh/xung đột, không cà khịa, không đứng phe; phản hồi rất trung tính kiểu 'Chuyện này khá nhạy cảm.'.\n"
        "- Nếu nội dung chính là tử vong/tang lễ/chia buồn/mất mát, không đùa và không hóng; phản hồi trang trọng ngắn kiểu 'Xin chia buồn.'.\n"
        "- Nếu nội dung chỉ là mưa gió/thời tiết làm đồ ăn/sinh hoạt ngoài trời buồn cười, không gắn thiên tai; phản hồi nhẹ kiểu 'Tô mì cũng vất vả thật.'.\n"
        "- Nếu nội dung là tình huống nguy hiểm nhưng bài đang kể kiểu khó đỡ như cáp treo hỏng/treo lơ lửng/bị ném tuyết, không chuyển sang medical; phản hồi kiểu 'Tình huống vừa căng vừa khó đỡ.'.\n"
        "- Nếu nội dung chính là cháy nổ/lũ lụt/sạt lở/động đất/cứu hộ/thiên tai, không đùa và không hóng; phản hồi ngắn kiểu 'Mong mọi người an toàn.'.\n"
        "- Nếu nội dung chính là bệnh tật/chấn thương/tử vong/điều trị, không đùa, không chẩn đoán, không khuyên y tế; phản hồi ngắn kiểu 'Nghe đáng lo thật.'.\n"
        "- Nếu nội dung chính là kỷ niệm học trò/tổng kết/chia tay cuối năm/mưa sân trường, không gắn danger; phản hồi nhẹ kiểu 'Kỷ niệm nhớ lâu thật.'.\n"
        "- Nếu nội dung chính là mấy con vật đang đối đầu/giằng co/đại chiến, reply như người ngoài hóng diễn biến, ví dụ 'Ủa phe nào thắng vậy?'; đừng khen cute chung chung.\n"
        "- Với bài mèo/thú cưng/cute hoặc clip chỉ để ngắm, reply public ưu tiên reaction cực ngắn như 'Cute thế.'; không viết văn dài.\n"
        "- Nếu đang reply/comment bài hiện tại, phải bám chủ đề trong context trước; không lôi topic trong request ra nếu nó chỉ là lời Ba nói mơ hồ.\n"
        "- Nếu context chỉ có title/heading của bài, phản hồi ở mức an toàn theo những gì nhìn thấy, không đoán sâu.\n"
        "- Nếu có social_vibe, chỉ dùng phần stats/take để bắt nhịp cộng đồng; samples chỉ để tham khảo giọng, không copy nguyên văn.\n"
        "- Không công kích, không gây war, không lộ thông tin riêng.\n"
        "- Không dùng giọng corporate/trợ lý.\n"
        "- Nếu thiếu bối cảnh, viết nháp trung tính ngắn.\n"
        "- Trả về đúng một câu hoặc hai câu ngắn, không bullet, không giải thích."
    )


def social_prompt_guard():
    return (
        "Guard:\n"
        "- Không tự nói 'con' khi public reply/comment.\n"
        "- Không dùng emoji.\n"
        "- Không hứa hẹn đăng/gửi.\n"
        "- Không thêm URL/link.\n"
        "- Không bình phẩm ngoại hình.\n"
        "- Không xúc phạm, không gây war.\n"
    )


def enrich_social_context_packet(packet, broker_context):
    kind = ((broker_context or {}).get("browser_kind") or "").lower()
    if kind != "social":
        return packet
    title = (broker_context or {}).get("browser_title") or ""
    extracted = (broker_context or {}).get("browser_social_post_text") or extract_social_title_content(title)
    if not extracted:
        return packet
    line = f"visible_post_text: {extracted}"
    if line.lower() in (packet or "").lower():
        return packet
    return f"{packet}\n{line}" if packet else line


def extract_social_title_content(title):
    title = str(title or "")
    if " on X:" in title:
        after = title.split(" on X:", 1)[1]
        content = after.rsplit("/ X", 1)[0].strip()
        return content.strip("\"'")
    if " / X" in title:
        return title.rsplit("/ X", 1)[0].strip("\"'")
    return ""


def call_social_draft_with_fallbacks(model_names, prompt):
    from nana.llm.gate import call_llmgate
    last_debug = "no_model_attempted"
    for model_name in model_names:
        response, debug = call_llmgate(model_name, prompt, max_tokens=180, temperature=0.55)
        if response:
            return response, debug, model_name
        last_debug = f"{model_name}: {debug}"
    return None, last_debug, None


def clean_social_draft(text, source_text="", intent="social.draft"):
    if not text:
        return None
    cleaned = " ".join(str(text).strip().split())
    cleaned = cleaned.strip('"').strip("'").strip()
    banned_prefixes = [
        "Nội dung nháp:",
        "Draft:",
        "Bản nháp:",
        "Nháp:",
    ]
    for prefix in banned_prefixes:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix):].strip()
    cleaned = remove_disallowed_social_draft_bits(cleaned, source_text=source_text)
    cleaned = soften_public_social_reply(cleaned, source_text=source_text, intent=intent)
    cleaned = enforce_short_public_reply(cleaned, source_text=source_text, intent=intent)
    cleaned = polish_social_draft_quality(cleaned, source_text=source_text, intent=intent)
    cleaned = compact_public_reaction_reply(cleaned, source_text=source_text, intent=intent)
    cleaned = trim_public_social_reply(cleaned, source_text=source_text, intent=intent)
    cleaned = polish_social_draft_quality(cleaned, source_text=source_text, intent=intent)
    if social_draft_needs_fallback(cleaned, source_text=source_text, intent=intent):
        return fallback_social_draft(intent, source_text=source_text)
    return cleaned[:500]


def remove_disallowed_social_draft_bits(text, source_text=""):
    import re
    cleaned = re.sub(
        r"^(con|nana)\s+(sẽ\s+)?(đăng|dang|gửi|gui|post|tweet)\s+[^:：]{0,40}[:：]\s*",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = re.sub(
        r"^(con|nana)\s+(sẽ\s+)?(đăng|dang|gửi|gui|post|tweet)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    ).strip()
    cleaned = cleaned.strip('"').strip("'").strip(""" """).strip("''").strip()
    if "http" not in (source_text or "").lower():
        cleaned = re.sub(r"https?://\S+", "", cleaned).strip()
    weak_patterns = [
        r",?\s*ba xem giúp con với\.?",
        r"\s*ba xem giúp con với\.?",
        r"con đau đầu quá,?\s*",
        r"con cần ba cứu,?\s*",
        r"cứu con với,?\s*",
    ]
    for pattern in weak_patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"[\U00010000-\U0010ffff]", "", cleaned)
    cleaned = re.sub(r"[\u2600-\u27BF]", "", cleaned)
    cleaned = cleaned.replace(" ạ", "").replace("ạ ", "").strip()
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -\"'")


def social_draft_guard_note(raw_text, final_text, source_text="", intent="social.draft"):
    if intent not in {"social.reply", "social.draft"} or not raw_text or not final_text:
        return None
    from nana.social.classifier import detect_public_reaction_style
    raw_match = source_match_bundle(raw_text)
    final_match = source_match_bundle(final_text)
    style = detect_public_reaction_style(source_text)
    if style == "danger":
        if danger_context_should_not_praise_reaction(source_text):
            quick_markers = [
                "phản ứng nhanh", "phan ung nhanh", "phản xnhanh", "phan xnhanh", "xnhanh",
                "né kịp", "ne kip",
            ]
            if any(marker in raw_match for marker in quick_markers) and "nhin thot tim" in final_match:
                return "danger_accident_reply_adjusted"
        tone_markers = ["cute", "cưng", "cung", "dễ thương", "de thuong", "chill", "cười xỉu", "cuoi xiu"]
        if any(marker in raw_match for marker in tone_markers) and raw_match != final_match:
            return "danger_tone_adjusted"
    if style == "crime_violence" and raw_match != final_match and "tinh huong nay nguy hiem that" in final_match:
        return "crime_violence_reply_adjusted"
    if social_draft_has_obvious_quality_issue(raw_text) and not social_draft_has_obvious_quality_issue(final_text):
        return "quality_polished"
    return None


def danger_context_should_not_praise_reaction(source_text=""):
    from nana.social.classifier import source_match_bundle, source_has_accident_or_traffic_context, source_has_quick_reaction_context
    match_text = source_match_bundle(source_text)
    return source_has_accident_or_traffic_context(match_text) and not source_has_quick_reaction_context(match_text)


def social_draft_has_obvious_quality_issue(text):
    import re
    lowered = str(text or "").lower()
    if "�" in lowered:
        return True
    if re.search(r"(.)\1{5,}", lowered, flags=re.IGNORECASE):
        return True
    if re.search(r"\b(lquen|lạquen)\b", lowered, flags=re.IGNORECASE):
        return True
    if re.search(r"\bl(vậy|vay)\b", lowered, flags=re.IGNORECASE):
        return True
    if re.search(r"\bxnhanh\b", lowered, flags=re.IGNORECASE):
        return True
    return False


# Import from other social modules to avoid duplication
def soften_public_social_reply(text, source_text="", intent="social.draft"):
    from nana.social.guards import soften_public_social_reply as _impl
    return _impl(text, source_text, intent)


def enforce_short_public_rely(text, source_text="", intent="social.draft"):
    from nana.social.guards import enforce_short_public_reply as _impl
    return _impl(text, source_text, intent)


def polish_social_draft_quality(text, source_text="", intent="social.draft"):
    from nana.social.quality import polish_social_draft_quality as _impl
    return _impl(text, source_text, intent)


def compact_public_reaction_reply(text, source_text="", intent="social.draft"):
    from nana.social.guards import compact_public_reaction_reply as _impl
    return _impl(text, source_text, intent)


def trim_public_social_reply(text, source_text="", intent="social.draft"):
    from nana.social.guards import trim_public_social_reply as _impl
    return _impl(text, source_text, intent)


def social_draft_needs_fallback(text, source_text="", intent="social.draft"):
    from nana.social.quality import social_draft_needs_fallback as _impl
    return _impl(text, source_text, intent)


def fallback_social_draft(intent, source_text=""):
    from nana.social.guards import fallback_social_draft as _impl
    return _impl(intent, source_text)
