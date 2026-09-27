"""nana.core.context_recovery — deterministic context recovery helpers."""
from __future__ import annotations


def context_recovery_next_step(context=None, missing_reason=None, vision_description=None):
    reason = str(missing_reason or "").lower()
    kind = ((context or {}).get("browser_kind") or "").lower()
    if vision_description:
        return "Vision đã có mô tả; nếu text/ảnh lệch nhau thì crop lại đúng vùng rồi chạy lại."
    if "browser_kind=" in reason and "social" not in reason:
        return "Tab hiện tại chưa phải bài social. Ba mở đúng tweet/bài rồi chạy /br lại."
    if "social_context_empty" in reason:
        return "Tab social đang mở nhưng chưa bắt được nội dung bài. Ba scroll/focus vào bài chính rồi /br lại."
    if kind != "social":
        return "Mở đúng bài social hoặc gửi nội dung trực tiếp để Nana soạn nháp an toàn."
    return "Bổ sung context còn thiếu rồi chạy lại /social-draft-test."


def vision_text_reconcile_report(context=None, vision_description=None):
    text = " ".join(
        str(x or "")
        for x in [
            (context or {}).get("browser_title"),
            (context or {}).get("browser_social_post_text"),
            (context or {}).get("browser_local_summary"),
        ]
    )
    text_groups = _signal_groups(text)
    vision_groups = _signal_groups(vision_description or "")
    shared = _shared_tokens(text, vision_description or "")
    overlap = _overlap_ratio(text_groups, vision_groups)
    if not vision_description:
        status = "missing_vision"
        note = "chưa có vision description"
    elif text_groups and vision_groups and text_groups.isdisjoint(vision_groups) and not _compatible_signal_groups(text_groups, vision_groups):
        status = "conflict"
        note = "text và vision lệch nhóm tín hiệu"
    else:
        status = "aligned"
        note = "text và vision không có xung đột rõ"
    return {
        "status": status,
        "text_groups": sorted(text_groups),
        "vision_groups": sorted(vision_groups),
        "shared": shared[:8],
        "overlap": overlap,
        "note": note,
    }


def _signal_groups(value):
    import re

    text = str(value or "").lower()
    token_set = set(re.findall(r"[\w\u00c0-\u1ef9]+", text))
    groups = set()
    markers = {
        "danger": ["tai nạn", "tai nan", "nguy hiểm", "nguy hiem", "crash", "accident", "cháy", "chay"],
        "vehicle": ["xe", "oto", "ô tô", "car", "motorcycle", "xe máy", "xe may", "hẻm", "hem", "cột điện", "cot dien"],
        "animal": ["mèo", "meo", "cat", "dog", "chó", "cho"],
        "music": ["music", "nhạc", "nhac", "song", "cover", "remix"],
        "social": ["tweet", "post", "comment", "reply", "facebook", "x.com"],
        "code": ["code", "bug", "traceback", "github", "pull request"],
        "image": ["ảnh", "anh", "photo", "image"],
        "video": ["video", "clip", "youtube"],
        "space_launch": ["starship", "spacex", "rocket", "launch", "tên lửa", "ten lua"],
        "anime_game": ["anime", "honkai", "star rail", "gái", "gai", "tóc", "toc", "pixel", "game"],
    }
    for group, words in markers.items():
        if any(_marker_matches(text, token_set, word) for word in words):
            groups.add(group)
    return groups


def _marker_matches(text, token_set, marker):
    marker = str(marker or "").lower().strip()
    if not marker:
        return False
    if " " in marker or "." in marker:
        return marker in text
    return marker in token_set


def _compatible_signal_groups(text_groups, vision_groups):
    left = set(text_groups)
    right = set(vision_groups)
    media_art = {"image", "anime_game"}
    if "music" in left and right & media_art:
        return True
    if "anime_game" in left and right & {"image", "music", "video"}:
        return True
    return False


def _shared_tokens(left, right):
    import re

    def tokens(value):
        return {
            token
            for token in re.findall(r"[\w\u00c0-\u1ef9]+", str(value or "").lower())
            if len(token) >= 3
        }

    return sorted(tokens(left) & tokens(right))


def _overlap_ratio(text_groups, vision_groups):
    if not text_groups or not vision_groups:
        return 0.0
    shared = set(text_groups) & set(vision_groups)
    union = set(text_groups) | set(vision_groups)
    return len(shared) / max(1, len(union))
