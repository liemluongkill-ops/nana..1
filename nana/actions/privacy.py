import re
from dataclasses import dataclass, field
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_QUERY_KEYS = {
    "access_token",
    "api_key",
    "apikey",
    "auth",
    "code",
    "key",
    "password",
    "secret",
    "session",
    "token",
}

SENSITIVE_TAB_MARKERS = [
    "bank",
    "banking",
    "checkout",
    "dang nhap",
    "login",
    "mat khau",
    "mật khẩu",
    "otp",
    "password",
    "payment",
    "paypal",
    "thanh toan",
    "thanh toán",
    "vietcombank",
    "mbbank",
    "techcombank",
    "bidv",
    "agribank",
]

FIELD_LIMITS = {
    "kind": 80,
    "title": 260,
    "url": 520,
    "heading": 260,
    "meta": 520,
    "selected_text": 900,
    "local_summary": 360,
    "social_post_text": 520,
    "social_vibe": 700,
}


@dataclass
class PrivacyFinding:
    kind: str
    count: int = 0
    severity: str = "low"


@dataclass
class PrivacyReport:
    source: str
    original_chars: int
    sanitized_chars: int
    sanitized_text: str
    risk: str
    allowed_for_external_model: bool
    findings: list[PrivacyFinding] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)


@dataclass
class ContextBudgetPreview:
    max_chars: int
    used_chars: int
    level: str
    level_name: str
    risk: str
    allowed_for_external_model: bool
    requires_confirm: bool
    packet: str
    included: list[str] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    findings: list[PrivacyFinding] = field(default_factory=list)
    blocked_reasons: list[str] = field(default_factory=list)


CONTEXT_LEVELS = {
    "L0": {
        "name": "identity",
        "fields": ("kind", "title", "url"),
        "max_chars": 700,
        "requires_confirm": False,
    },
    "L1": {
        "name": "light_context",
        "fields": ("kind", "title", "url", "heading", "meta"),
        "max_chars": 1200,
        "requires_confirm": False,
    },
    "L2": {
        "name": "focus",
        "fields": ("kind", "title", "url", "heading", "selected_text", "local_summary", "social_post_text", "social_vibe"),
        "max_chars": 1600,
        "requires_confirm": False,
    },
    "L3": {
        "name": "visible_short",
        "fields": ("kind", "title", "url", "heading", "meta", "selected_text", "local_summary", "social_post_text", "social_vibe"),
        "max_chars": 2400,
        "requires_confirm": False,
    },
    "L4": {
        "name": "full_text_locked",
        "fields": ("kind", "title", "url", "heading", "meta", "selected_text", "local_summary", "social_post_text", "social_vibe"),
        "max_chars": 5000,
        "requires_confirm": True,
    },
}


def build_privacy_report(text, source="manual"):
    text = text or ""
    redacted, findings = redact_sensitive_text(text)
    risk, allowed, blocked = classify_privacy_result(redacted, findings)
    return PrivacyReport(
        source=source,
        original_chars=len(text),
        sanitized_chars=len(redacted),
        sanitized_text=redacted,
        risk=risk,
        allowed_for_external_model=allowed,
        findings=findings,
        blocked_reasons=blocked,
    )


def build_context_budget_preview(context, max_chars=None, level=None):
    context = context or {}
    selected_level = normalize_context_level(level, context)
    level_policy = CONTEXT_LEVELS[selected_level]
    max_chars = max_chars or level_policy["max_chars"]
    raw_items = [
        ("kind", context.get("browser_kind")),
        ("title", context.get("browser_title")),
        ("url", sanitize_url(context.get("browser_url"))),
        ("heading", context.get("browser_heading")),
        ("meta", context.get("browser_meta_description")),
        ("selected_text", context.get("browser_selected_text")),
        ("local_summary", context.get("browser_local_summary")),
        ("social_post_text", context.get("browser_social_post_text")),
        ("social_vibe", context.get("browser_social_vibe")),
    ]
    allowed_fields = set(level_policy["fields"])

    included = []
    dropped = []
    lines = []
    for field_name, raw_value in raw_items:
        if field_name not in allowed_fields:
            dropped.append(field_name)
            continue
        value = normalize_value(raw_value)
        if not value:
            continue
        value = trim_text(value, FIELD_LIMITS.get(field_name, 240))
        line = f"{field_name}: {value}"
        candidate = "\n".join(lines + [line])
        if len(candidate) > max_chars:
            dropped.append(field_name)
            continue
        lines.append(line)
        included.append(field_name)

    packet_raw = "\n".join(lines)
    report = build_privacy_report(packet_raw, source="context_preview")
    tab_risk = classify_tab_sensitivity(context)
    risk = max_risk(report.risk, tab_risk)
    blocked = list(report.blocked_reasons)
    if tab_risk == "high":
        blocked.append("sensitive_tab")

    allowed = risk != "high"
    return ContextBudgetPreview(
        max_chars=max_chars,
        used_chars=len(report.sanitized_text),
        level=selected_level,
        level_name=level_policy["name"],
        risk=risk,
        allowed_for_external_model=allowed and not level_policy["requires_confirm"],
        requires_confirm=level_policy["requires_confirm"],
        packet=report.sanitized_text,
        included=included,
        dropped=dropped,
        findings=report.findings,
        blocked_reasons=unique_list(blocked),
    )


def normalize_context_level(level, context=None):
    if level:
        candidate = str(level).upper().strip()
        if candidate in CONTEXT_LEVELS:
            return candidate
    context = context or {}
    if context.get("browser_selected_text"):
        return "L2"
    if context.get("browser_heading") or context.get("browser_meta_description"):
        return "L1"
    return "L0"


def redact_sensitive_text(text):
    text = text or ""
    findings = []

    text, count = replace_pattern(
        text,
        r"sk-[A-Za-z0-9_\-]{16,}",
        "[REDACTED_OPENAI_KEY]",
        flags=0,
    )
    add_finding(findings, "openai_key", count, "high")

    text, count = replace_pattern(
        text,
        r"(?i)\bBearer\s+[A-Za-z0-9._\-]{16,}",
        "Bearer [REDACTED_TOKEN]",
    )
    add_finding(findings, "bearer_token", count, "high")

    text, count = replace_pattern(
        text,
        r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}",
        "[REDACTED_JWT]",
    )
    add_finding(findings, "jwt", count, "high")

    text, count = replace_pattern(
        text,
        r"(?i)\b(api[_ -]?key|apikey|secret|token|password|passwd|pwd)\b\s*[:=]\s*([^\s,;\"']{8,})",
        lambda match: f"{match.group(1)}=[REDACTED_SECRET]",
    )
    add_finding(findings, "labeled_secret", count, "high")

    text, count = redact_luhn_numbers(text)
    add_finding(findings, "payment_card_like", count, "high")

    text, count = replace_pattern(
        text,
        r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.+-])",
        "[REDACTED_EMAIL]",
    )
    add_finding(findings, "email", count, "medium")

    text, count = replace_pattern(
        text,
        r"(?<!\d)(?:\+?84|0)(?:[\s.-]?\d){8,10}(?!\d)",
        "[REDACTED_PHONE]",
    )
    add_finding(findings, "phone", count, "medium")

    return text, findings


def classify_tab_sensitivity(context):
    haystack = " ".join(
        normalize_value(context.get(key))
        for key in ("browser_url", "browser_title", "browser_heading")
    ).lower()
    haystack = strip_vietnamese_diacritics(haystack)
    if any(marker in haystack for marker in SENSITIVE_TAB_MARKERS):
        return "high"
    return "low"


def classify_privacy_result(text, findings):
    severities = [finding.severity for finding in findings if finding.count > 0]
    risk = "low"
    if "high" in severities:
        risk = "high"
    elif "medium" in severities:
        risk = "medium"

    blocked = []
    if risk == "high":
        blocked.append("high_severity_secret")
    return risk, risk != "high", blocked


def sanitize_url(url):
    url = normalize_value(url)
    if not url:
        return url
    try:
        parts = urlsplit(url)
        query = []
        changed = False
        for key, value in parse_qsl(parts.query, keep_blank_values=True):
            if key.lower() in SENSITIVE_QUERY_KEYS:
                query.append((f"{key}_redacted", "1"))
                changed = True
            else:
                query.append((key, value))
        if not changed:
            return url
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
    except Exception:
        return url


def replace_pattern(text, pattern, replacement, flags=re.MULTILINE):
    return re.subn(pattern, replacement, text, flags=flags)


def redact_luhn_numbers(text):
    count = 0

    def replace(match):
        nonlocal count
        raw = match.group(0)
        digits = re.sub(r"\D", "", raw)
        if 13 <= len(digits) <= 19 and looks_like_payment_card_context(text, match) and luhn_valid(digits):
            count += 1
            return "[REDACTED_CARD]"
        return raw

    redacted = re.sub(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", replace, text)
    return redacted, count


def looks_like_payment_card_context(text, match):
    start, end = match.span()
    before = text[max(0, start - 16):start].lower()
    after = text[end:min(len(text), end + 16)].lower()

    # Social/video IDs inside URLs can be 18-19 digits and sometimes pass Luhn.
    # Treat contiguous URL/path IDs as public identifiers, not payment cards.
    urlish_markers = (
        "://",
        "/status/",
        "/statuses/",
        "/post/",
        "/posts/",
        "/watch/",
        "status/",
        "tweet/",
        "x.com/",
        "twitter.com/",
        "t.co/",
        "youtube.com/",
        "youtu.be/",
    )
    context = before + after
    if any(marker in context for marker in urlish_markers):
        return False
    if start > 0 and text[start - 1] in "/#?=&._-":
        return False
    if end < len(text) and text[end] in "/#?=&._-":
        return False
    return True


def luhn_valid(digits):
    total = 0
    reverse_digits = digits[::-1]
    for index, char in enumerate(reverse_digits):
        value = int(char)
        if index % 2 == 1:
            value *= 2
            if value > 9:
                value -= 9
        total += value
    return total % 10 == 0


def add_finding(findings, kind, count, severity):
    if count:
        findings.append(PrivacyFinding(kind=kind, count=count, severity=severity))


def trim_text(text, limit):
    text = normalize_value(text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def normalize_value(value):
    if value is None:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ").strip()


def max_risk(*risks):
    order = {"low": 0, "medium": 1, "high": 2}
    return max((risk for risk in risks if risk), key=lambda risk: order.get(risk, 0), default="low")


def unique_list(values):
    seen = set()
    result = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def strip_vietnamese_diacritics(text):
    replacements = {
        "à": "a", "á": "a", "ạ": "a", "ả": "a", "ã": "a",
        "â": "a", "ầ": "a", "ấ": "a", "ậ": "a", "ẩ": "a", "ẫ": "a",
        "ă": "a", "ằ": "a", "ắ": "a", "ặ": "a", "ẳ": "a", "ẵ": "a",
        "è": "e", "é": "e", "ẹ": "e", "ẻ": "e", "ẽ": "e",
        "ê": "e", "ề": "e", "ế": "e", "ệ": "e", "ể": "e", "ễ": "e",
        "ì": "i", "í": "i", "ị": "i", "ỉ": "i", "ĩ": "i",
        "ò": "o", "ó": "o", "ọ": "o", "ỏ": "o", "õ": "o",
        "ô": "o", "ồ": "o", "ố": "o", "ộ": "o", "ổ": "o", "ỗ": "o",
        "ơ": "o", "ờ": "o", "ớ": "o", "ợ": "o", "ở": "o", "ỡ": "o",
        "ù": "u", "ú": "u", "ụ": "u", "ủ": "u", "ũ": "u",
        "ư": "u", "ừ": "u", "ứ": "u", "ự": "u", "ử": "u", "ữ": "u",
        "ỳ": "y", "ý": "y", "ỵ": "y", "ỷ": "y", "ỹ": "y",
        "đ": "d",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
        text = text.replace(source.upper(), target.upper())
    return text
