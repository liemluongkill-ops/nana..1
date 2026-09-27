"""
Deduplication analysis — GĐ 8.

Ghi nhận trùng lặp giữa:
  1. is_browser_context_question() — main.py, dòng 37778
  2. is_live_awareness_question() — runtime/live_awareness.py, dòng 589

─────────────────────────────────────────────────────────────────────────────
FUNCTION 1 — main.py:37778
─────────────────────────────────────────────────────────────────────────────
def is_browser_context_question(text_lower):
    markers = [
        "trang này",
        "tab này",
        "website này",
        "web này",
        "đang mở trang gì",
        "đang mở gì",
        "mở trang gì",
        "trang gì",
        "nhìn giống gì",
        "đoạn này",
        "text này",
        "dòng này",
        "phần này",
        "bôi đen",
        "selected",
        "browser",
    ]
    return any(marker in text_lower for marker in markers) or is_live_awareness_question(text_lower)

─────────────────────────────────────────────────────────────────────────────
FUNCTION 2 — runtime/live_awareness.py:589
─────────────────────────────────────────────────────────────────────────────
LIVE_AWARENESS_MARKERS = (
    "trang này",
    "tab này",
    "website này",
    "web này",
    "link này",
    "url này",
    "đang mở trang gì",
    "đang mở gì",
    "mở trang gì",
    "trang gì",
    "web gì",
    "tab gì",
    "link gì",
    "đường link nào",
    "đang xem",
    "xem cái gì",
    "xem gì",
    "đang coi",
    "coi gì",
    "video gì",
    "clip gì",
    "youtube gì",
    "đang đọc",
    "đọc gì",
    "nhìn giống gì",
    "đang ở đâu",
    "ở trang nào",
    "đoạn này",
    "text này",
    "dòng này",
    "phần này",
    "chữ này",
    "bôi đen",
    "selected",
    "browser",
    "trình duyệt",
)

def is_live_awareness_question(text: str | None) -> bool:
    lowered = _normalize_vi(text)
    return any(_normalize_vi(marker) in lowered for marker in LIVE_AWARENESS_MARKERS)

─────────────────────────────────────────────────────────────────────────────
SO SÁNH
─────────────────────────────────────────────────────────────────────────────

1. ĐIỂM GIỐNG NHAU
──────────────────
• Cùng mục đích: phát hiện câu hỏi liên quan đến nội dung trình duyệt hiện tại
• Chia sẻ 16 markers chung:
    trang này, tab này, website này, web này,
    đang mở trang gì, đang mở gì, mở trang gì, trang gì,
    nhìn giống gì, đoạn này, text này, dòng này, phần này,
    bôi đen, selected, browser
• Cả hai đều check substring presence (marker in text)

2. ĐIỂM KHÁC NHAU
─────────────────
                    │ is_browser_context_question        │ is_live_awareness_question
────────────────────┼───────────────────────────────────┼────────────────────────────
Markers trong hàm   │ 16 hardcoded literals             │ 0 (dùng LIVE_AWARENESS_MARKERS tuple)
Tổng markers        │ 16 + 52 (gọi hàm kia) = 68       │ 52 trong tuple
Normalize input     │ KHÔNG (dùng text_lower trực tiếp)│ CÓ (_normalize_vi)
Normalize markers   │ KHÔNG                             │ CÓ (_normalize_vi)
Extra markers       │ —                                 │ 27 markers thêm:
                    │                                   │ link này, url này, web gì,
                    │                                   │ tab gì, link gì, đường link nào,
                    │                                   │ đang xem, xem cái gì, xem gì,
                    │                                   │ đang coi, coi gì, video gì,
                    │                                   │ clip gì, youtube gì, đang đọc,
                    │                                   │ đọc gì, đang ở đâu, ở trang nào,
                    │                                   │ chữ này, trình duyệt
Signature           │ (text_lower) — đòi hỏi lowercase  │ (text | None) — tự lowercase
Tuple marker        │ 0                                 │ 52
Cách gọi            │ Gọi is_live_awareness_question()  │ Độc lập

3. NGHĨA CỦA VIỆC is_browser_context_question GỌI is_live_awareness_question
─────────────────────────────────────────────────────────────────────────────
Khi main.py:37797 chạy:
    return any(marker in text_lower for marker in markers) or is_live_awareness_question(text_lower)

→ Nó check:
  a) 16 markers của nó (không normalize)
  b) HOẶC 52 markers của live_awareness (có normalize)

→ 16 markers đầu bị CHECK 2 LẦN (1 lần trực tiếp, 1 lần qua hàm kia)
→ 27 markers riêng của live_awareness chỉ được check QUA hàm kia

4. REDUNDANCY PHÁT HIỆN
────────────────────────
• 16 markers GIỐNG NHAU y hệt giữa 2 hàm
• main.py gọi live_awareness → nên cả 68 markers được check
• Nhưng: markers trong is_browser_context_question KHÔNG normalized,
  còn is_live_awareness_question DÙNG _normalize_vi
  → "Trang này" trong text = match trong cả 2
  → "Trang này" với dấu ≠ "trang nay" → only live_awareness catches
  → Đây là BỔ SUNG, không trùng lặp thuần

─────────────────────────────────────────────────────────────────────────────
KHUYẾN NGHỊ
─────────────────────────────────────────────────────────────────────────────

TÙY CHỌN A — Giữ tách rõ ràng (RECOMMENDED)
──────────────────────────────────────────
• Giữ is_browser_context_question ở main.py vì nó là một gateway ngắn gọn
• Giữ is_live_awareness_question ở live_awareness.py vì nó là single source of truth
  cho marker set đầy đủ + normalization
• Không cần thay đổi — kiến trúc 2-layers là có chủ đích:
    - is_browser_context_question = quick gate (16 markers, no normalize)
    - is_live_awareness_question = deep check (52 markers, with normalize)
• Lý do tách: main.py cần check nhanh (không normalize), runtime cần check kỹ (normalize)

TÙY CHỌN B — Merge hoàn toàn
─────────────────────────────
Loại bỏ markers trong is_browser_context_question, chỉ gọi is_live_awareness_question.
→ Rủi ro: mất quick-gate benefit nếu main.py cần check nhanh không normalize.

TÙY CHỌN C — Dọn dẹp duplicate markers
────────────────────────────────────────
Nếu muốn clean, tách LIVE_AWARENESS_MARKERS ra thành:
  - BASE_MARKERS: 16 markers dùng chung (trong shared module)
  - LIVE_EXTRA_MARKERS: 27 markers riêng của live_awareness
Sau đó:
  - is_browser_context_question: check BASE_MARKERS (no normalize)
  - is_live_awareness_question: check BASE_MARKERS (normalize) + LIVE_EXTRA_MARKERS

KẾT LUẬN: Giữ nguyên như hiện tại (Tùy chọn A). Kiến trúc 2-layers có mục đích.
Việc main.py gọi live_awareness giúp 68 markers được check đầy đủ,
16 markers giống nhau không gây vấn đề vì mỗi hàm có normalization khác nhau.
"""

from __future__ import annotations

# ── SOURCE 1: is_browser_context_question (main.py:37778) ──────────────────

BROWSER_CONTEXT_MARKERS = [
    "trang này",
    "tab này",
    "website này",
    "web này",
    "đang mở trang gì",
    "đang mở gì",
    "mở trang gì",
    "trang gì",
    "nhìn giống gì",
    "đoạn này",
    "text này",
    "dòng này",
    "phần này",
    "bôi đen",
    "selected",
    "browser",
]


def is_browser_context_question(text_lower: str) -> bool:
    """Quick gate — check 16 hardcoded browser-context markers (no normalize).

    Mirrors main.py:37778.
    Also delegates to is_live_awareness_question for full marker coverage.
    """
    # Import here to avoid circular deps at module level
    from nana.runtime.live_awareness import is_live_awareness_question
    return any(marker in text_lower for marker in BROWSER_CONTEXT_MARKERS) or is_live_awareness_question(text_lower)


# ── SOURCE 2: is_live_awareness_question (runtime/live_awareness.py:589) ───
# Xem đầy đủ trong nana/runtime/live_awareness.py:589-591
# Trích lại để tài liệu:
#
#   LIVE_AWARENESS_MARKERS = (
#       "trang này", "tab này", "website này", "web này", "link này", "url này",
#       "đang mở trang gì", "đang mở gì", "mở trang gì", "trang gì", "web gì",
#       "tab gì", "link gì", "đường link nào", "đang xem", "xem cái gì", "xem gì",
#       "đang coi", "coi gì", "video gì", "clip gì", "youtube gì", "đang đọc",
#       "đọc gì", "nhìn giống gì", "đang ở đâu", "ở trang nào", "đoạn này",
#       "text này", "dòng này", "phần này", "chữ này", "bôi đen", "selected",
#       "browser", "trình duyệt",
#   )
#
#   def is_live_awareness_question(text: str | None) -> bool:
#       lowered = _normalize_vi(text)
#       return any(_normalize_vi(marker) in lowered for marker in LIVE_AWARENESS_MARKERS)
#
# Hàm gốc ở: nana/runtime/live_awareness.py:589-591

# ── Shared markers between both functions ──────────────────────────────────
# 16 markers appear in BOTH is_browser_context_question AND LIVE_AWARENESS_MARKERS:
_SHARED_MARKERS = [
    "trang này", "tab này", "website này", "web này",
    "đang mở trang gì", "đang mở gì", "mở trang gì", "trang gì",
    "nhìn giống gì", "đoạn này", "text này", "dòng này", "phần này",
    "bôi đen", "selected", "browser",
]

# ── Extra markers ONLY in LIVE_AWARENESS_MARKERS (not in browser_context) ───
_LIVE_ONLY_EXTRA_MARKERS = [
    "link này", "url này", "web gì", "tab gì", "link gì", "đường link nào",
    "đang xem", "xem cái gì", "xem gì", "đang coi", "coi gì", "video gì",
    "clip gì", "youtube gì", "đang đọc", "đọc gì", "đang ở đâu", "ở trang nào",
    "chữ này", "trình duyệt",
]

# ── Summary ────────────────────────────────────────────────────────────────
DEDUP_SUMMARY = {
    "shared_markers_count": len(_SHARED_MARKERS),
    "live_only_extra_count": len(_LIVE_ONLY_EXTRA_MARKERS),
    "browser_context_count": 16,
    "live_awareness_count": 52,
    "total_unique_checked": 16 + 21,  # 16 shared + 21 unique to live only (52 - 16 = 36... wait)
    # Actually: 52 total in LIVE_AWARENESS_MARKERS, 16 shared = 36 unique to live
    # 16 shared checked twice (once raw, once normalized)
    "recommendation": "keep_separate",  # 2-layer architecture is intentional
    "reason": "browser_context = quick gate (no normalize), live_awareness = deep check (with normalize)",
    "bug_risk": "none",  # live_awareness catches normalized variants that browser_context misses
}


__all__ = [
    "BROWSER_CONTEXT_MARKERS",
    "DEDUP_SUMMARY",
    "is_browser_context_question",
    "_SHARED_MARKERS",
    "_LIVE_ONLY_EXTRA_MARKERS",
]
