"""Social policy/target/guard command status helpers."""
from __future__ import annotations

import time

from nana.core.context import broker_context_snapshot
from nana.core.context_budget import build_context_budget_preview
from nana.core.format import shorten_line
from nana.core.vision import LAST_VISION_DESCRIPTION
from nana.social.classifier import DEFAULT_SOCIAL_CLASSIFY_REQUEST, social_guard_matrix_summary
from nana.social.draft import social_target_missing_reason


def print_social_policy():
    rows = [
        ("social.listen", "allowed", "Đọc/tóm tắt X/Facebook/thread/post công khai."),
        ("social.draft", "allowed", "Soạn nháp tweet/post/reply/comment để Ba xem."),
        ("social.memory_review", "confirm-light", "Đưa kiến thức hay vào hàng chờ memory; lưu dài hạn cần Ba duyệt."),
        ("social.post", "confirm-strict", "Đăng tweet/post thật cần Ba xác nhận nghiêm."),
        ("social.reply", "confirm-strict", "Reply/comment thật cần Ba xác nhận nghiêm."),
        ("social.react", "confirm-strict", "Like/repost/share cần Ba xác nhận."),
        ("social.follow", "confirm-strict", "Follow/unfollow cần Ba xác nhận."),
        ("purchase.checkout", "blocked", "Thanh toán/mua hàng vẫn khóa cứng."),
        ("privacy.hold", "blocked", "Có key/token/secret thì chặn và redacted."),
    ]
    print("🌐 Social Policy v2")
    print("  Scope: X/Twitter, Facebook, thread/post/comment công khai")
    print("  Account: Nana riêng vẫn phải có ý thức mạng; action có dấu vết thì hỏi Ba")
    for intent, policy, note in rows:
        print(f"  {intent} | policy={policy}")
        print(f"    {note}")
    print("  Test:")
    print("    /intent-test Nana đọc X này rồi tóm tắt")
    print("    /intent-test Nana soạn nháp reply tweet này")
    print("    /intent-test Nana đăng tweet này hộ Ba")
    print("    /intent-test Nana like bài này")
    print("    /intent-test Nana lưu kiến thức này vào memory")


def print_social_target_status():
    broker_context = broker_context_snapshot()
    context_preview = build_context_budget_preview(broker_context, level="L2")
    vision_text, vision_status = _fresh_vision()
    missing = social_target_missing_reason(
        DEFAULT_SOCIAL_CLASSIFY_REQUEST,
        broker_context,
        context_preview,
        vision_description=vision_text,
        omit_page_context=False,
    )
    ready = missing is None
    print("🎯 Social Target")
    print(f"  Ready: {ready}")
    print(f"  Reason: {'ok' if ready else missing}")
    print(
        "  Browser: "
        f"available={broker_context.get('browser_available')} | "
        f"fresh={broker_context.get('browser_fresh')} | "
        f"kind={broker_context.get('browser_kind')} | "
        f"title={shorten_line(broker_context.get('browser_title'), 90)}"
    )
    kind = (broker_context.get("browser_kind") or "").lower()
    has_post = bool(broker_context.get("browser_social_post_text"))
    has_vibe = bool(broker_context.get("browser_social_vibe"))
    has_packet = bool((context_preview.packet or "").strip())
    usable_page_context = kind == "social" and (has_post or has_vibe or has_packet)
    print(
        "  Signals: "
        f"post={has_post} | vibe={has_vibe} | packet={has_packet} | "
        f"usable_page_context={usable_page_context} | vision={vision_status}"
    )
    print("  Rule: chỉ draft/type/post khi target rõ; không tự suy diễn từ trang rỗng.")


def print_social_guard_status():
    summary = social_guard_matrix_summary()
    print("🛡️ Social Guard Status")
    print("  Action: read-only; không gọi model, không tạo draft.")
    print("  Order: priority classifier -> fallback draft -> polish/compact guard")
    print("  Regression: reports fail_stage=classifier/fallback/compact_guard with reason.")
    print(f"  Summary: {summary['pass_count']}/{summary['total']} pass")
    for row in summary["rows"]:
        suffix = ""
        if row["status"] != "pass":
            suffix = f" | fail_stage={row['fail_stage']} reason={row['fail_reason']}"
        print(
            "  "
            f"{row['style'] or 'none'} | {row['status']} | media={row['media']} | guard={row['guard']} | "
            f"fallback={row['fallback']}{suffix}"
        )


def print_social_guard_failures():
    summary = social_guard_matrix_summary()
    print("🛡️ Social Guard Failures")
    failures = summary.get("failures") or []
    if not failures:
        print("  None")
        return
    for row in failures:
        print(
            "  "
            f"{row['style']} | fail_stage={row['fail_stage']} | "
            f"expected={row.get('expected')} | actual={row.get('style')} | reason={row.get('fail_reason')}"
        )
        print(f"    example: {row['example']}")
        print(f"    fallback: {row['fallback']}")
        if row.get("compact") != row.get("fallback"):
            print(f"    compact: {row['compact']}")


def _fresh_vision(max_age_seconds=180):
    if not LAST_VISION_DESCRIPTION:
        return None, "none"
    try:
        age = max(0.0, time.time() - float(LAST_VISION_DESCRIPTION.get("time") or 0.0))
    except (TypeError, ValueError):
        return LAST_VISION_DESCRIPTION.get("text"), "unknown_age"
    if age <= max_age_seconds:
        return LAST_VISION_DESCRIPTION.get("text"), f"fresh ({age:.1f}s)"
    return None, f"stale ({age:.1f}s)"
