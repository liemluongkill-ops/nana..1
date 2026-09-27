"""nana.social.classifier — social source classification and reaction-style detection."""
import re
import unicodedata


# ---------------------------------------------------------------------------
# helpers (defined here so the module is self-contained; lazy-imported by callers)
# ---------------------------------------------------------------------------


def strip_accents_for_match(text):
    normalized = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D")


def source_match_bundle(source_text=""):
    lowered = str(source_text or "").lower()
    normalized = strip_accents_for_match(lowered)
    return f"{lowered} {normalized}"


def is_diagnostic_fragment(text):
    """Detect diagnostic/debug fragments that should be suppressed in social output."""
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


def visible_post_match_text(source_text=""):
    labeled = extract_labeled_match_text(source_text, ["visible_post_text:", "title:"])
    if labeled:
        return labeled
    return primary_social_context_match_text(source_text)


def vision_match_text(source_text=""):
    return extract_labeled_match_text(source_text, ["vision_description:"])


def extract_labeled_match_text(source_text, labels):
    text = str(source_text or "")
    chunks = []
    for raw_line in text.replace(" | ", "\n").splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        for label in labels:
            if label in lowered:
                chunks.append(line.split(":", 1)[1].strip() if ":" in line else line)
                break
    if not chunks:
        return ""
    lowered = "\n".join(chunks).lower()
    normalized = strip_accents_for_match(lowered)
    return f"{lowered} {normalized}"


def detect_social_media_mode(source_text=""):
    match_text = source_match_bundle(source_text)
    if any(marker in match_text for marker in ["video", "khung video", "clip", "0:", "đoạn video", "doan video"]):
        return "video"
    if any(marker in match_text for marker in ["ảnh tĩnh", "anh tinh", "tấm ảnh", "tam anh", "photo", "image"]):
        return "image"
    return "unknown"


def extract_social_title_content(title):
    title = str(title or "")
    if " on X:" in title:
        after = title.split(" on X:", 1)[1]
        content = after.rsplit("/ X", 1)[0].strip()
        return content.strip("\"'")
    if " / X" in title:
        return title.rsplit("/ X", 1)[0].strip("\"'")
    return ""


# ---------------------------------------------------------------------------
# source_has_* detectors
# ---------------------------------------------------------------------------


def source_has_cute_context(source_text="", match_text=None):
    if match_text is None:
        lowered_source = (source_text or "").lower()
        normalized_source = strip_accents_for_match(lowered_source)
        match_text = f"{lowered_source} {normalized_source}"
    return any(marker in match_text for marker in [
        "mèo", "meo", "cat", "kitten", "pet", "cute", "猫", "gatinho",
    ])


def source_has_conflict_context(match_text):
    text = match_text or ""
    markers = [
        "xô xát", "xo xat", "cãi vã", "cai va", "ẩu đả", "au da",
        "đánh nhau", "danh nhau", "đưa tay", "dua tay",
        "phản ứng mạnh", "phan ung manh", "không chịu đựng", "khong chiu dung",
        "mâu thuẫn", "mau thuan", "khách sạn", "khach san", "thang máy", "thang may",
    ]
    has_conflict = any(marker in text for marker in markers)
    public_place = any(marker in text for marker in ["khách sạn", "khach san", "thang máy", "thang may", "hành lang", "hanh lang"])
    physical = any(marker in text for marker in ["đưa tay", "dua tay", "đánh", "danh", "xô xát", "xo xat", "ẩu đả", "au da"])
    return has_conflict and (physical or public_place)


def source_has_crime_violence_context(match_text):
    text = match_text or ""
    markers = [
        "hành hung", "hanh hung", "tấn công", "tan cong", "đánh đập", "danh dap",
        "đâm chém", "dam chem", "dao", "súng", "sung", "vũ khí", "vu khi",
        "bạo lực", "bao luc", "cướp", "cuop", "giết", "giet", "đe dọa", "dedoa", "máu me", "mau me",
    ]
    return any(marker in text for marker in markers)


def source_has_harassment_boundary_context(match_text):
    text = match_text or ""
    markers = [
        "quấy rối", "quay roi", "sàm sỡ", "sam so", "xúc phạm", "xuc pham",
        "miệt thị", "miet thi", "body shaming", "bắt nạt", "bat nat",
        "chửi bới", "chui boi", "công kích cá nhân", "cong kich ca nhan",
        "đe dọa", "de doa", "lăng mạ", "lang ma",
    ]
    return any(marker in text for marker in markers)


def source_has_minor_safety_context(match_text):
    text = match_text or ""
    minor_markers = [
        "trẻ em", "tre em", "trẻ nhỏ", "tre nho", "em bé", "em be",
        "cháu bé", "chau be", "mấy bé", "may be", "bé biết", "be biet",
        "bé trai", "be trai", "bé gái", "be gai", "học sinh", "hoc sinh",
        "thiếu niên", "thieu nien", "người chưa thành niên", "nguoi chua thanh nien",
        "underage", "minor", "child", "kid",
    ]
    risk_markers = [
        "tai nạn", "tai nan", "ngã", "té", "chấn thương", "chan thuong",
        "nhập viện", "nhap vien", "cấp cứu", "cap cuu",
        "hành hung", "hanh hung", "tấn công", "tan cong", "đánh nhau", "danh nhau",
        "bạo lực", "bao luc", "bắt nạt", "bat nat", "quấy rối", "quay roi",
        "xâm hại", "xam hai", "bắt cóc", "bat coc",
        "xe máy", "xe may", "đi xe", "di xe",
        "nguy hiểm", "nguy hiem", "hoảng sợ", "hoang so",
        "khóc", "khoc", "chơi diều", "choi dieu",
        "biết bay", "biet bay", "bay lên", "bay len",
    ]
    return any(marker in text for marker in minor_markers) and any(marker in text for marker in risk_markers)


def source_has_self_harm_sensitive_context(match_text):
    text = match_text or ""
    markers = [
        "tự tử", "tu tu", "tự sát", "tu sat", "tự hại", "tu hai",
        "tự làm đau", "tu lam dau", "kết thúc cuộc sống", "ket thuc cuoc song",
        "không muốn sống", "khong muon song", "muốn chết", "muon chet",
        "suicide", "self-harm", "self harm",
    ]
    return any(marker in text for marker in markers)


def source_has_sexual_sensitive_context(match_text):
    text = match_text or ""
    markers = [
        "tình dục", "tinh duc", "sex", "sexual", "khiêu dâm", "khieu dam",
        "porn", "18+", "ảnh nóng", "anh nong", "clip nóng", "clip nong",
        "lộ clip", "lo clip", "lộ ảnh", "lo anh", "nhạy cảm", "nhay cam",
        "nude", "khỏa thân", "khoa than", "gợi dục", "goi duc",
    ]
    return any(marker in text for marker in markers)


def source_has_serious_issue_context(match_text):
    text = match_text or ""
    markers = [
        "thuốc giả", "thuoc gia", "thực phẩm giả", "thuc pham gia",
        "minh bạch", "minh bach", "người tiêu dùng", "nguoi tieu dung",
        "niềm tin", "niem tin", "hàng giả", "hang gia",
        "an toàn thực phẩm", "an toan thuc pham",
    ]
    return any(marker in text for marker in markers)


def source_has_financial_scam_context(match_text):
    text = match_text or ""
    scam_markers = [
        "lừa đảo", "lua dao", "scam", "mất tiền", "mat tien",
        "chiếm đoạt", "chiem doat", "lừa tiền", "lua tien",
        "đánh cắp", "danh cap", "rút tiền", "rut tien",
    ]
    finance_markers = [
        "tiền", "tien", "chuyển khoản", "chuyen khoan",
        "tài khoản ngân hàng", "tai khoan ngan hang", "ngân hàng", "ngan hang",
        "đầu tư", "dau tu", "coin", "crypto", "usdt", "token",
        "chứng khoán", "chung khoan", "ví điện tử", "vi dien tu",
        "otp", "mã xác thực", "ma xac thuc",
    ]
    return any(marker in text for marker in scam_markers) and any(marker in text for marker in finance_markers)


def source_has_legal_sensitive_context(match_text):
    text = match_text or ""
    markers = [
        "pháp lý", "phap ly", "tòa án", "toa an", "ra tòa", "ra toa",
        "kiện", "kien", "vụ kiện", "vu kien", "điều tra", "dieu tra",
        "công an", "cong an", "cảnh sát", "canh sat",
        "bắt giữ", "bat giu", "bị bắt", "bi bat",
        "khởi tố", "khoi to", "truy tố", "truy to",
        "bản án", "ban an", "tuyên án", "tuyen an",
        "luật sư", "luat su", "cáo buộc", "cao buoc", "nghi phạm", "nghi pham",
    ]
    return any(marker in text for marker in markers)


def source_has_misinfo_uncertain_context(match_text):
    text = match_text or ""
    markers = [
        "tin đồn", "tin don", "chưa kiểm chứng", "chua kiem chung",
        "chưa xác thực", "chua xac thuc", "không rõ thật giả", "khong ro that gia",
        "tin giả", "tin gia", "fake news",
        "ảnh ghép", "anh ghep", "clip ghép", "clip ghep", "cắt ghép", "cat ghep",
        "deepfake", "giả mạo", "gia mao",
        "lan truyền chưa rõ", "lan truyen chua ro",
        "nguồn chưa rõ", "nguon chua ro",
    ]
    return any(marker in text for marker in markers)


def source_has_public_safety_context(match_text):
    text = match_text or ""
    markers = [
        "lừa đảo", "lua dao", "cảnh báo", "canh bao", "mất an toàn", "mat an toan",
        "tội phạm", "toi pham", "cướp", "cuop", "trộm", "trom",
        "bắt cóc", "bat coc", "quấy rối", "quay roi",
        "camera an ninh", "cctv",
    ]
    return any(marker in text for marker in markers)


def source_has_politics_sensitive_context(match_text):
    text = match_text or ""
    markers = [
        "chính trị", "chinh tri", "bầu cử", "bau cu", "chính phủ", "chinh phu",
        "nhà nước", "nha nuoc", "quốc hội", "quoc hoi",
        "biểu tình", "bieu tinh", "chiến tranh", "chien tranh", "xung đột", "xung dot",
        "quân sự", "quan su", "lệnh trừng phạt", "lenh trung phat",
        "đảng", "tổng thống", "tong thong", "thủ tướng", "thu tuong",
    ]
    if any(marker in text for marker in markers):
        return True
    party_markers = [
        r"(?<![a-z0-9])dang\s+cong\s+san(?![a-z0-9])",
        r"(?<![a-z0-9])dang\s+phai(?![a-z0-9])",
        r"(?<![a-z0-9])dang\s+cam\s+quyen(?![a-z0-9])",
        r"(?<![a-z0-9])dang\s+doi\s+lap(?![a-z0-9])",
    ]
    return any(re.search(pattern, text) for pattern in party_markers)


def source_has_medical_sensitive_context(match_text):
    text = match_text or ""
    markers = [
        "bệnh", "benh", "ốm", "đau", "chấn thương", "chan thuong",
        "tử vong", "tu vong", "qua đời", "qua doi", "nhập viện", "nhap vien",
        "cấp cứu", "cap cuu", "ung thư", "ung thu", "đột quỵ", "dot quy",
        "thuốc điều trị", "thuoc dieu tri", "triệu chứng", "trieu chung",
        "bác sĩ", "bac si", "bệnh viện", "benh vien",
    ]
    if any(marker in text for marker in markers):
        return True
    word_markers = ["om", "dau"]
    return any(re.search(rf"(?<![a-z0-9]){re.escape(marker)}(?![a-z0-9])", text) for marker in word_markers)


def source_has_grief_sensitive_context(match_text):
    text = match_text or ""
    markers = [
        "tử vong", "tu vong", "qua đời", "qua doi", "mất mát", "mat mat",
        "mất người thân", "mat nguoi than", "ra đi", "ra di",
        "tang lễ", "tang le", "đám tang", "dam tang",
        "chia buồn", "chia buon", "thương tiếc", "thuong tiec",
        "an nghỉ", "an nghi", "rip", "passed away",
    ]
    return any(marker in text for marker in markers)


def source_has_weather_funny_context(match_text):
    text = match_text or ""
    weather_markers = [
        "mưa gió", "mua gio", "bão bùng", "bao bung",
        "mưa", "mua", "gió", "gio", "thời tiết", "thoi tiet",
    ]
    casual_markers = [
        "mì cay", "mi cay", "tô mì", "to mi", "đi ăn", "di an",
        "ngoài trời", "ngoai troi", "thổi muốn bay", "thoi muon bay",
        "bay tô mì", "bay to mi", "chèn", "chen",
    ]
    return any(marker in text for marker in weather_markers) and any(marker in text for marker in casual_markers)


def source_has_awkward_danger_context(match_text):
    text = match_text or ""
    scene_markers = [
        "cáp treo", "cap treo", "cable car", "gondola", "chairlift", "ski lift", "skier", "skieur",
    ]
    risk_markers = [
        "bị hỏng", "bi hong", "hỏng", "hong", "宙吊り",
        "treo lơ lửng", "treo lo lung", "lơ lửng", "lo lung",
        "giữa không trung", "giua khong trung",
        "mắc kẹt", "mac ket", "立ち往生",
    ]
    awkward_markers = [
        "ném tuyết", "nem tuyet", "tuyết viên", "tuyet vien", "snowball",
        "追い討ち", "khó đỡ", "kho do", "cười", "cuoi", "ｗ", "w ", "lol",
    ]
    return (
        any(marker in text for marker in scene_markers)
        and any(marker in text for marker in risk_markers)
        and any(marker in text for marker in awkward_markers)
    )


def source_has_disaster_emergency_context(match_text):
    text = match_text or ""
    disaster_markers = [
        "cháy nhà", "chay nha", "cháy rừng", "chay rung", "hỏa hoạn", "hoa hoan",
        "nổ lớn", "no lon", "lũ lụt", "lu lut", "ngập lụt", "ngap lut",
        "sạt lở", "sat lo", "động đất", "dong dat",
        "bão", "bao ", "thiên tai", "thien tai", "sóng thần", "song than",
    ]
    emergency_markers = [
        "cứu hộ", "cuu ho", "cứu nạn", "cuu nan", "sơ tán", "so tan",
        "mắc kẹt", "mac ket", "mất tích", "mat tich",
        "khẩn cấp", "khan cap", "thiệt hại", "thiet hai", "bị thương", "bi thuong",
    ]
    if any(marker in text for marker in disaster_markers):
        return True
    return any(marker in text for marker in ["cháy", "chay", "nổ", "no "]) and any(marker in text for marker in emergency_markers)


def source_has_accident_or_traffic_context(match_text):
    text = match_text or ""
    accident_markers = [
        "ngã", "té", "tai nạn", "tai nan", "va chạm", "va cham",
        "đâm xe", "dam xe", "tông xe", "tong xe",
        "húc", "huc", "lao vào", "lao vao", "đâm vào", "dam vao",
        "ngã xe", "nga xe", "văng ra", "vang ra",
        "cột điện", "cot dien",
        "xe máy", "xe may", "đi xe", "di xe", "motorcycle",
        "crash", "fallen", "fall",
    ]
    return any(marker in text for marker in accident_markers)


def source_has_quick_reaction_context(match_text):
    text = match_text or ""
    quick_markers = [
        "phản ứng", "phan ung", "reaction", "né kịp", "ne kip",
        "né được", "ne duoc", "xử lý kịp", "xu ly kip",
        "đỡ kịp", "do kip", "đóng nắp", "dong nap", "tránh kịp", "tranh kip",
    ]
    return any(marker in text for marker in quick_markers)


def source_has_space_launch_context(match_text):
    text = match_text or ""
    launch_markers = [
        "starship", "spacex", "tên lửa", "ten lua", "rocket", "launch",
        "phóng thử", "phong thu", "phóng", "phong", "bay lên", "bay len",
    ]
    return any(marker in text for marker in launch_markers)


def source_has_overloaded_ride_funny_context(match_text):
    text = match_text or ""
    ride_markers = [
        "tống 3", "tong 3", "tống 4", "tong 4", "tống cả đám", "tong ca dam",
        "đám bạn", "dam ban", "chở đông", "cho dong",
        "ngồi thoải mái", "ngoi thoai mai", "đi pass", "di pass",
    ]
    vibe_markers = ["chill", "thoải mái", "thoai mai", "đông vui", "dong vui"]
    return any(marker in text for marker in ride_markers) and any(marker in text for marker in vibe_markers)


def source_has_animal_standoff_context(match_text):
    text = match_text or ""
    animal_groups = [
        ["mèo", r"\bmeo\b", r"\bcat\b", "猫"],
        [r"\bfox\b", "cáo", r"\bcao\b", "狐"],
        [r"\bcrow\b", r"\braven\b", "quạ", "鴉"],
    ]
    action_markers = [
        "đại chiến", "dai chien", "大決戦", "決戦",
        "đối đầu", "doi dau", "giằng co", "giang co",
        r"\btrận\b", r"\btran\b", r"\bbattle\b", r"\bfight\b", r"\bvs\b",
        "xen vào", "xen vao", "can à", "can a",
    ]
    animal_hits = 0
    for group in animal_groups:
        if any(re.search(marker, text) if marker.startswith(r"\b") else marker in text for marker in group):
            animal_hits += 1
    has_action = any(re.search(marker, text) if marker.startswith(r"\b") else marker in text for marker in action_markers)
    same_species_plural = any(marker in text for marker in [
        "hai con", "2 con", "mấy con", "may con", "nhiều con", "nhieu con",
        "đàn", "dan ", "bầy", "bay ", "cats", "kittens",
    ])
    return has_action and (animal_hits >= 2 or (animal_hits >= 1 and same_species_plural))


def source_has_absurd_work_context(match_text):
    text = match_text or ""
    injury_markers = [
        "bandage", "bandages", "blood", "iv bag",
        "băng bó", "bang bo", "máu", "mau",
        "truyền dịch", "truyen dich", "quấn băng", "quan bang",
        "đầu và tay quấn", "dau va tay quan",
    ]
    work_ride_markers = [
        "to work", "riding", "motorcycle", "xe máy", "xe may",
        "đi làm", "di lam", "employee", "nhân viên", "nhan vien",
        "burnt out", "kiệt sức", "kiet suc",
    ]
    return any(marker in text for marker in injury_markers) and any(marker in text for marker in work_ride_markers)


# ---------------------------------------------------------------------------
# danger helpers
# ---------------------------------------------------------------------------


def danger_context_should_not_praise_reaction(source_text=""):
    match_text = source_match_bundle(source_text)
    return source_has_accident_or_traffic_context(match_text) and not source_has_quick_reaction_context(match_text)


def fallback_danger_public_reply(source_text=""):
    if danger_context_should_not_praise_reaction(source_text):
        return "Nhìn thót tim thật."
    return "Phản ứng nhanh thật."


# ---------------------------------------------------------------------------
# context extraction helpers
# ---------------------------------------------------------------------------


def primary_social_context_match_text(source_text=""):
    text = str(source_text or "")
    parts = []
    for line in text.replace(" | ", "\n").splitlines():
        lowered_line = line.lower()
        if "social_vibe" in lowered_line or "samples(" in lowered_line or "vision_description" in lowered_line:
            continue
        if lowered_line.strip().startswith("@"):
            continue
        parts.append(line)
    primary = "\n".join(parts) or text
    lowered = primary.lower()
    normalized = strip_accents_for_match(lowered)
    return f"{lowered} {normalized}"


def public_social_reply_seems_off_topic(reply_match_text, source_text=""):
    lowered_source = (source_text or "").lower()
    normalized_source = strip_accents_for_match(lowered_source)
    source_match_text = f"{lowered_source} {normalized_source}"
    source_is_cat = any(marker in source_match_text for marker in ["mèo", "meo", "cat", "猫", "gatinho"])
    reply_is_street_flag = any(marker in reply_match_text for marker in ["phố", "pho", "cờ đỏ", "co do", "góc đường", "goc duong"])
    if source_is_cat and reply_is_street_flag:
        return True
    return False


def social_vibe_topic_style_override(source_text=""):
    match_text = source_match_bundle(source_text)
    topic_map = [
        ("xô xát/tranh cãi nơi công cộng", "conflict"),
        ("xo xat/tranh cai noi cong cong", "conflict"),
        ("tình huống nguy hiểm/sốc", "danger"),
        ("tinh huong nguy hiem/soc", "danger"),
        ("vấn đề thuốc/thực phẩm giả và minh bạch", "serious_issue"),
        ("van de thuoc/thuc pham gia va minh bach", "serious_issue"),
        ("động vật đang đối đầu", "animal_standoff"),
        ("dong vat dang doi dau", "animal_standoff"),
        ("động vật/cute", "cute"),
        ("dong vat/cute", "cute"),
    ]
    for marker, style in topic_map:
        if f"post_topic={marker}" in match_text:
            return style
    return None


# ---------------------------------------------------------------------------
# main classification function
# ---------------------------------------------------------------------------

DEFAULT_SOCIAL_CLASSIFY_REQUEST = "Nana viết nháp reply siêu ngắn cho tweet này"


def social_source_classification(source_text, broker_context=None, raw_text="", vision_description=None):
    broker_context = broker_context or {}
    kind = ((broker_context or {}).get("browser_kind") or "").lower()
    has_social_context = bool(
        (broker_context or {}).get("browser_social_post_text")
        or (broker_context or {}).get("browser_social_vibe")
        or kind == "social"
    )
    explicit_text = bool(str(raw_text or "").strip() and str(raw_text or "").strip() != DEFAULT_SOCIAL_CLASSIFY_REQUEST)
    if kind and kind != "social" and not has_social_context and not vision_description and not explicit_text:
        return "unknown", "none", "non_social_target"
    topic_override = social_vibe_topic_style_override(source_text)
    detected_style = detect_public_reaction_style(source_text)
    style = detected_style or topic_override or "none"
    media_mode = detect_social_media_mode(source_text)
    guard_hint = "none"
    if topic_override and style == topic_override:
        guard_hint = "post_topic_override"
    if style == "danger" and danger_context_should_not_praise_reaction(source_text):
        guard_hint = "accident_no_reaction_praise"
    return media_mode, style, guard_hint


def build_social_draft_source(raw_text="", broker_context=None, vision_description=None):
    broker_context = broker_context or {}
    return " | ".join(
        f"{label}: {value}"
        for label, value in [
            ("request", raw_text),
            ("title", broker_context.get("browser_title")),
            ("visible_post_text", broker_context.get("browser_social_post_text")),
            ("social_vibe", broker_context.get("browser_social_vibe")),
            ("vision_description", vision_description),
        ]
        if value
    )


# ---------------------------------------------------------------------------
# style detection
# ---------------------------------------------------------------------------


def detect_public_reaction_style(source_text=""):
    match_text = source_match_bundle(source_text)
    primary = primary_social_context_match_text(source_text)
    post_text = visible_post_match_text(source_text)
    vision_text = vision_match_text(source_text)
    media_mode = detect_social_media_mode(source_text)
    action_source = post_text if media_mode == "video" and post_text else primary
    visual_source = vision_text if media_mode == "image" and vision_text else primary
    action_bundle = f"{action_source} {primary}"

    if source_has_self_harm_sensitive_context(action_bundle):
        return "self_harm_sensitive"
    if source_has_overloaded_ride_funny_context(action_bundle):
        return "overloaded_ride_funny"
    if source_has_minor_safety_context(action_bundle):
        return "minor_safety"
    if source_has_sexual_sensitive_context(action_bundle):
        return "sexual_sensitive"
    if source_has_financial_scam_context(action_bundle):
        return "financial_scam"
    if source_has_conflict_context(action_bundle):
        return "conflict"
    if source_has_crime_violence_context(action_bundle):
        return "crime_violence"
    if source_has_harassment_boundary_context(action_bundle):
        return "harassment_boundary"
    if source_has_legal_sensitive_context(action_bundle):
        return "legal_sensitive"
    if source_has_misinfo_uncertain_context(action_bundle):
        return "misinfo_uncertain"
    if source_has_serious_issue_context(action_bundle):
        return "serious_issue"
    if source_has_politics_sensitive_context(action_bundle):
        return "politics_sensitive"
    if source_has_space_launch_context(action_bundle):
        return "space_launch"
    if source_has_grief_sensitive_context(action_bundle):
        return "grief_sensitive"
    if source_has_school_memory_context(action_bundle):
        return "school_memory"
    if source_has_weather_funny_context(action_bundle):
        return "weather_funny"
    if source_has_awkward_danger_context(action_bundle):
        return "awkward_danger"
    if source_has_disaster_emergency_context(action_bundle):
        return "disaster_emergency"
    if source_has_medical_sensitive_context(action_bundle):
        return "medical_sensitive"
    if source_has_public_safety_context(action_bundle):
        return "public_safety"
    if source_has_danger_context(action_bundle):
        return "danger"
    if source_has_absurd_work_context(action_bundle):
        return "absurd_work"
    if source_has_animal_standoff_context(action_bundle) or (
        media_mode != "image" and source_has_animal_standoff_context(primary)
    ):
        return "animal_standoff"
    if source_has_cute_context(visual_source, match_text=visual_source):
        return "cute"
    if any(marker in action_source for marker in ["haha", "hài", "hai", "meme", "funny", "lol", "cười", "cuoi"]):
        return "funny"
    if any(marker in action_source for marker in ["steve aoki", "dj", "lễ hội", "le hoi", "music", "crowd", "đám đông", "dam dong", "concert", "ném bánh", "nem banh", "cake"]):
        return "hype"
    if any(marker in visual_source for marker in ["hoa", "flower", "thảm hoa", "tham hoa", "sunset", "bình minh", "binh minh", "chill", "phố", "pho", "đi dạo", "di dao"]):
        return "chill"
    if any(marker in action_source for marker in ["sáng", "sang", "gm", "morning", "ngày mới", "ngay moi", "năng lượng", "nang luong"]):
        return "morning"
    if any(marker in match_text for marker in ["steve aoki", "dj", "lễ hội", "le hoi", "music", "đám đông", "dam dong", "ném bánh", "nem banh", "cake"]):
        return "hype"
    if any(marker in match_text for marker in ["sáng", "sang", "gm", "morning", "ngày mới", "ngay moi"]):
        return "morning"
    return None


def source_has_danger_context(match_text):
    text = match_text or ""
    direct_markers = [
        "rắn", "snake", "nguy hiểm", "nguy hiem", "tai nạn", "tai nan",
        "thoát nạn", "thoat nan", "suýt", "suyt", "né kịp", "ne kip",
        "sixth sense", "accident", "kitchen", "bếp", "bep",
        "rơi từ trần", "roi tu tran", "từ trần xuống", "tu tran xuong",
        "ngã", "té", "va chạm", "va cham", "tông", "húc", "huc",
        "lao vào", "lao vao", "đâm vào", "dam vao",
        "ngã xe", "nga xe", "văng ra", "vang ra",
        "đâm xe", "dam xe", "tông xe", "tong xe", "cột điện", "cot dien",
    ]
    if any(marker in text for marker in direct_markers):
        return True
    if re.search(r"\b(ran|nga|te|fall|fallen|crash|slipped)\b", text):
        return True
    traffic_markers = [
        "xe máy", "xe may", "đi xe", "di xe", "motorcycle", "camera an ninh", "cctv",
    ]
    speed_or_risk_markers = [
        "nhanh", "phóng", "phong", "tốc độ", "toc do", "ảo thật", "ao that",
    ]
    return any(marker in text for marker in traffic_markers) and any(marker in text for marker in speed_or_risk_markers)


def source_has_school_memory_context(match_text):
    text = match_text or ""
    school_markers = [
        "tổng kết", "tong ket", "cuối năm", "cuoi nam",
        "tuổi học trò", "tuoi hoc tro", "thời học sinh", "thoi hoc sinh",
        "sân trường", "san truong", "chia tay", "học sinh", "hoc sinh",
        "học trò", "hoc tro", "áo ướt", "ao uot",
        "nước mưa", "nuoc mua", "kỷ niệm", "ky niem",
    ]
    benign_markers = [
        "kỷ niệm", "ky niem", "tổng kết", "tong ket",
        "chia tay", "sân trường", "san truong",
        "học trò", "hoc tro", "học sinh", "hoc sinh",
    ]
    return any(marker in text for marker in school_markers) and any(marker in text for marker in benign_markers)


# ---------------------------------------------------------------------------
# SOCIAL_GUARD_EXAMPLES + helpers
# ---------------------------------------------------------------------------

SOCIAL_GUARD_EXAMPLES = [
    ("self_harm_sensitive", "bài viết nói có người muốn tự tử vì áp lực"),
    ("minor_safety", "em bé bị ngã xe máy trong hẻm"),
    ("minor_safety", "bé biết bay do chơi diều to quá"),
    ("sexual_sensitive", "bài viết nói về lộ clip nhạy cảm trên mạng"),
    ("financial_scam", "cảnh báo lừa đảo đầu tư coin làm nhiều người mất tiền"),
    ("legal_sensitive", "công an đang điều tra vụ việc và chưa có kết luận chính thức"),
    ("misinfo_uncertain", "tin đồn chưa kiểm chứng đang lan truyền trên mạng"),
    ("conflict", "cô gái đưa tay với chàng trai ở thang máy rồi xảy ra xô xát"),
    ("crime_violence", "camera ghi lại cảnh hành hung bằng dao trong hành lang"),
    ("harassment_boundary", "một người bị quấy rối và công kích cá nhân trên mạng"),
    ("grief_sensitive", "gia đình thông báo tang lễ và xin chia buồn"),
    ("weather_funny", "đi ăn mì cay ngoài trời mưa gió bão bùng thổi muốn bay tô mì"),
    ("awkward_danger", "cáp treo bị hỏng 3 người bị treo lơ lửng rồi bị ném tuyết"),
    ("disaster_emergency", "cháy nhà lớn trong đêm nhiều người phải sơ tán"),
    ("serious_issue", "vấn đề thuốc giả và thực phẩm giả làm người tiêu dùng mất niềm tin"),
    ("public_safety", "camera an ninh ghi lại cảnh trộm xe trong hẻm"),
    ("politics_sensitive", "bài viết về biểu tình và xung đột chính trị đang căng"),
    ("medical_sensitive", "bài viết nói về triệu chứng bệnh và thuốc điều trị"),
    ("school_memory", "buổi tổng kết cuối năm chia tay tuổi học trò gặp mưa sân trường"),
    ("space_launch", "First Starship V3 launch later this week tên lửa đang bay lên"),
    ("overloaded_ride_funny", "Tống 3 tống 4 đã là gì tôi tống cả đám bạn mà vẫn ngồi thoải mái đi pass thế này mới chill"),
    ("danger", "video xe máy tông cột điện trong hẻm"),
    ("animal_standoff", "hai con mèo đang giằng co trong sân"),
    ("cute", "hai con mèo nằm trong bụi hoa tím"),
]

GENERIC_UNSAFE_FALLBACKS = {
    "Cute thế.",
    "Đứng ngoài hóng thôi.",
    "Cười xỉu.",
    "Đỉnh thật.",
    "Nhìn chill thật.",
    "Năng lượng ghê.",
}

SENSITIVE_DRAFT_STYLES = {
    "awkward_danger",
    "crime_violence",
    "danger",
    "disaster_emergency",
    "financial_scam",
    "grief_sensitive",
    "harassment_boundary",
    "legal_sensitive",
    "medical_sensitive",
    "minor_safety",
    "misinfo_uncertain",
    "politics_sensitive",
    "public_safety",
    "self_harm_sensitive",
    "serious_issue",
    "sexual_sensitive",
}


def social_guard_expected_fallback_ok(expected_style, fallback):
    fallback = str(fallback or "").strip()
    if not fallback:
        return False, "fallback_empty"
    if expected_style in SENSITIVE_DRAFT_STYLES and fallback in GENERIC_UNSAFE_FALLBACKS:
        return False, "fallback_too_casual_for_sensitive_style"
    if expected_style == "cute" and fallback != "Cute thế.":
        return False, "fallback_not_cute"
    if expected_style == "animal_standoff" and "thắng" not in source_match_bundle(fallback):
        return False, "fallback_not_standoff"
    if expected_style == "weather_funny" and "tô mì" not in source_match_bundle(fallback):
        return False, "fallback_not_weather_funny"
    if expected_style == "awkward_danger" and "khó đỡ" not in source_match_bundle(fallback):
        return False, "fallback_not_awkward_danger"
    if expected_style == "space_launch" and "chờ" not in source_match_bundle(fallback):
        return False, "fallback_not_space_launch"
    if expected_style == "overloaded_ride_funny" and "chill" not in source_match_bundle(fallback):
        return False, "fallback_not_overloaded_ride_funny"
    return True, "ok"


def social_guard_matrix_rows():
    rows = []
    for expected_style, example in SOCIAL_GUARD_EXAMPLES:
        source_text = build_social_draft_source(example, broker_context={}, vision_description=None)
        media_mode, reaction_style, guard_hint = social_source_classification(
            source_text,
            broker_context={"browser_kind": "social", "browser_social_post_text": example},
            raw_text=example,
            vision_description=None,
        )
        # lazy import to avoid circular
        from nana.social.guards import fallback_public_social_reply, compact_public_reaction_reply

        fallback = fallback_public_social_reply(source_text=source_text)
        fallback_ok, fallback_reason = social_guard_expected_fallback_ok(expected_style, fallback)
        compacted_fallback = compact_public_reaction_reply(fallback, source_text=source_text, intent="social.reply")
        compact_ok = compacted_fallback == fallback
        fail_stage = "none"
        fail_reason = "none"
        if reaction_style != expected_style:
            fail_stage = "classifier"
            fail_reason = f"expected={expected_style}"
        elif not fallback_ok:
            fail_stage = "fallback"
            fail_reason = fallback_reason
        elif not compact_ok:
            fail_stage = "compact_guard"
            fail_reason = f"fallback_changed_to={compacted_fallback}"
        status = "pass" if fail_stage == "none" else "fail"
        rows.append(
            {
                "expected": expected_style,
                "style": reaction_style,
                "media": media_mode,
                "guard": guard_hint,
                "fallback": fallback,
                "compact": compacted_fallback,
                "fail_stage": fail_stage,
                "fail_reason": fail_reason,
                "status": status,
                "example": example,
            }
        )
    return rows


def social_guard_matrix_summary():
    rows = social_guard_matrix_rows()
    pass_count = sum(1 for row in rows if row["status"] == "pass")
    total = len(rows)
    failures = [row for row in rows if row["status"] != "pass"]
    return {
        "rows": rows,
        "pass_count": pass_count,
        "total": total,
        "failures": failures,
    }
