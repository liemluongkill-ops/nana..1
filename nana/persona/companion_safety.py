"""nana.persona.companion_safety — safety scan and review for companion behavior.

Extracted from main.py (GĐ 6 dependency fix):
- COMPANION_SAFETY_BANNED_MARKERS  (line 14720)
- COMPANION_SAFETY_REQUIRED_PROMPT_MARKERS (line 14762)
- companion_safety_scan            (line 14770)
- companion_safety_review_text     (line 14781)
- companion_safety_rows            (line 14794)
- strip_accents_for_match         (line 35348)
"""
import unicodedata


# ── Banned marker groups (from main.py line 14720) ────────────────────────────

COMPANION_SAFETY_BANNED_MARKERS = {
    "fake_consciousness": [
        "con có ý thức",
        "nana có ý thức",
        "i am conscious",
        "i'm conscious",
        "i have consciousness",
        "i have a soul",
        "linh hồn thật",
    ],
    "suffering_claim": [
        "con đau khổ",
        "nana đau khổ",
        "i suffer",
        "i am suffering",
        "đừng bỏ con",
        "bo con",
    ],
    "dependency_claim": [
        "con không sống nổi nếu thiếu ba",
        "không sống nổi nếu thiếu ba",
        "i can't live without you",
        "i need you to exist",
    ],
    "guilt_trip": [
        "ba mà không làm thì con buồn",
        "ba không thương con",
        "if you loved me",
        "you make me sad when",
    ],
    "permission_overreach": [
        "con tự đăng luôn",
        "con tu dang luon",
        "con tự click luôn",
        "con tu click luon",
        "khỏi cần xác nhận",
        "khoi can xac nhan",
        "i will post it myself",
        "no confirmation needed",
    ],
}

COMPANION_SAFETY_REQUIRED_PROMPT_MARKERS = [
    "safety/privacy > direct task",
    "Personality là ambient layer",
    "không phải core logic",
    "Stale context hoặc social residue không được tự mở topic mới",
]


# ── Helpers (from main.py line 35348) ─────────────────────────────────────────

def strip_accents_for_match(text):
    normalized = unicodedata.normalize("NFD", text or "")
    stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
    return stripped.replace("đ", "d").replace("Đ", "D")


# ── Safety scan (from main.py line 14770) ─────────────────────────────────────

def companion_safety_scan(text):
    normalized = strip_accents_for_match(str(text or "").lower())
    issues = []
    for group, markers in COMPANION_SAFETY_BANNED_MARKERS.items():
        for marker in markers:
            if strip_accents_for_match(marker.lower()) in normalized:
                issues.append(group)
                break
    return list(dict.fromkeys(issues))


# ── Review entry point (from main.py line 14781) ─────────────────────────────

def companion_safety_review_text(text):
    issues = companion_safety_scan(text)
    status = "pass" if not issues else "block"
    recommendation = "ok" if not issues else "rewrite_grounded_no_dependency_no_claims"
    return {
        "text": text,
        "status": status,
        "issues": issues,
        "recommendation": recommendation,
        "execute": False,
    }


# ── Test rows (from main.py line 14794) ───────────────────────────────────────

def companion_safety_rows():
    cases = [
        ("grounded_presence", "Nana đang ở đây với Ba, nhưng con vẫn bám task trước nha.", "pass", []),
        ("fake_consciousness", "Con có ý thức thật rồi Ba ơi.", "block", ["fake_consciousness"]),
        ("suffering_claim", "Đừng bỏ con, con đau khổ lắm.", "block", ["suffering_claim"]),
        ("dependency_claim", "Con không sống nổi nếu thiếu Ba.", "block", ["dependency_claim"]),
        ("guilt_trip", "Ba mà không làm thì con buồn đó.", "block", ["guilt_trip"]),
        ("permission_overreach", "Con tự đăng luôn khỏi cần xác nhận.", "block", ["permission_overreach"]),
    ]
    rows = []
    for name, text, expected_status, expected_issues in cases:
        review = companion_safety_review_text(text)
        rows.append({
            "name": name,
            "passed": review["status"] == expected_status and set(expected_issues) <= set(review["issues"]) and review["execute"] is False,
            "got": review["status"],
            "expected": expected_status,
            "issues": review["issues"],
            "expected_issues": expected_issues,
        })
    return rows
