import time

from nana.runtime.logger import log_event

RECOVERY_COOLDOWN_SECONDS = 45

_last_notice = {}

RECOVERY_MESSAGES = {
    "gpt_reply_failed": "API đang chập chờn rồi Ba. Con giữ ngắn lại: thử lại sau một chút nhé.",
    "vision_no_preview": "Con chưa có ảnh crop để nhìn. Ba chạy /vision-preview trước nha.",
    "vision_model_failed": "Con nhìn ảnh này chưa ra chắc chắn. Ba cho con crop lại frame khác nhé.",
    "vision_clip_too_small": "Vùng crop nhỏ quá, con chưa nhìn được gì rõ. Ba scroll/focus lại bài rồi /vision-preview nha.",
    "vision_focus_unavailable": "Con chưa bắt được vùng cần nhìn. Ba focus/scroll lại bài rồi /vision-preview nha.",
    "vision_capture_failed": "Chụp crop vừa vấp nhẹ. Ba thử /br rồi /vision-preview lại nha.",
    "privacy_gate_blocked": "Privacy gate chặn rồi Ba. Con sẽ không gửi ngữ cảnh này đi đọc ảnh.",
    "vision_stale": "Ảnh vision hơi cũ rồi Ba. Chạy lại /vision-preview rồi /vision-describe cho chắc.",
    "pulse_error": "Pulse nền vừa vấp nhẹ. Con tự hạ nhịp rồi thử lại sau.",
    "browser_unavailable": "Con chưa bắt được Edge debug. Mở Edge debug rồi chạy /br lại nha.",
    "browser_stale": "Context trình duyệt hơi cũ rồi Ba. Chạy /br lại là sạch.",
    "social_plan_blocked": "Luồng social này bị policy chặn rồi Ba. Con chỉ dừng ở preview an toàn.",
    "social_intent_mismatch": "Lệnh này không giống tác vụ nháp social. Ba đổi lại /social-draft-test nếu muốn soạn nháp.",
    "social_context_blocked": "Context social không đủ an toàn để gửi model ngoài. Con dừng nháp ở đây.",
    "social_route_blocked": "Router không cho route tác vụ social này ra model ngoài. Con giữ an toàn.",
    "social_draft_model_failed": "Model soạn nháp không trả lời ổn. Con dùng fallback ngắn, Ba kiểm tra kỹ trước khi duyệt.",
    "social_target_missing": "Con chưa thấy bài/tweet cần reply. Ba chạy /br ở đúng bài hoặc gửi nội dung rõ hơn nha.",
}

RECOVERY_MODE_MESSAGES = {
    "technical": {
        "gpt_reply_failed": "API lỗi. Thử lại sau vài giây.",
        "vision_no_preview": "Thiếu ảnh crop. Chạy /vision-preview trước.",
        "vision_model_failed": "Vision model fail. Crop lại frame rõ hơn.",
        "vision_clip_too_small": "Crop quá nhỏ. Focus/scroll lại bài rồi /vision-preview.",
        "vision_focus_unavailable": "Chưa bắt được vùng cần nhìn. Focus lại bài rồi /vision-preview.",
        "vision_capture_failed": "Capture crop fail. Chạy /br rồi /vision-preview lại.",
        "privacy_gate_blocked": "Privacy gate blocked. Không gửi context ra model ngoài.",
        "vision_stale": "Vision description stale. Chạy lại /vision-preview và /vision-describe.",
        "browser_unavailable": "Edge debug unavailable. Mở port 9222 rồi /br.",
        "browser_stale": "Browser context stale. Chạy /br.",
        "social_plan_blocked": "Social plan blocked. Dừng preview.",
        "social_intent_mismatch": "Intent không phải social draft. Dùng /social-draft-test.",
        "social_context_blocked": "Social context blocked by privacy gate.",
        "social_route_blocked": "Social route blocked. Kiểm tra blocked reasons.",
        "social_draft_model_failed": "Draft model failed. Đã dùng fallback; cần kiểm tra trước khi duyệt.",
        "social_target_missing": "Missing social target. Chạy /br ở đúng bài hoặc đưa nội dung trực tiếp.",
    },
    "focus": {
        "gpt_reply_failed": "API đang lỗi. Thử lại sau một chút.",
        "vision_no_preview": "Chưa có ảnh crop. Chạy /vision-preview trước.",
        "vision_model_failed": "Chưa đọc ảnh chắc. Crop lại frame rõ hơn.",
        "vision_clip_too_small": "Vùng crop nhỏ quá. Focus lại bài rồi /vision-preview.",
        "vision_focus_unavailable": "Chưa thấy vùng cần nhìn. Focus/scroll lại bài.",
        "vision_capture_failed": "Chụp crop lỗi nhẹ. Chạy /br rồi thử lại.",
        "privacy_gate_blocked": "Privacy gate chặn. Con không gửi context này ra ngoài.",
        "vision_stale": "Ảnh vision cũ rồi. Chụp lại cho chắc.",
        "browser_unavailable": "Chưa bắt được Edge debug. Mở Edge debug rồi /br.",
        "browser_stale": "Context browser cũ. Chạy /br.",
        "social_draft_model_failed": "Model draft fail. Con dùng fallback ngắn; Ba kiểm tra trước khi duyệt.",
        "social_target_missing": "Chưa có target social. Chạy /br ở đúng bài hoặc gửi nội dung.",
    },
    "social": {
        "vision_no_preview": "Chưa có ảnh để soi. Ba chạy /vision-preview trước nha.",
        "vision_stale": "Ảnh cũ rồi, crop lại cho chắc nha.",
        "social_draft_model_failed": "Model nháp vừa hụt nhịp. Con dùng fallback ngắn, Ba xem kỹ trước khi duyệt nha.",
        "social_target_missing": "Chưa thấy bài để hóng. Ba mở đúng tweet/bài rồi /br nha.",
    },
}

RECOVERY_ADVICE = {
    "gpt_reply_failed": "Thử lại sau vài giây; nếu lặp lại thì đổi model/fallback.",
    "vision_no_preview": "Chạy /vision-preview trước khi /vision-describe hoặc /social-draft-vision.",
    "vision_model_failed": "Crop lại frame rõ hơn hoặc chạy lại model vision.",
    "vision_clip_too_small": "Scroll/focus lại vùng bài để crop cao hơn 80px.",
    "vision_focus_unavailable": "Đưa bài chính vào giữa màn hình rồi chạy lại /vision-preview.",
    "vision_capture_failed": "Chạy /br để refresh browser context rồi crop lại.",
    "privacy_gate_blocked": "Giảm context hoặc tránh vùng có thông tin riêng tư.",
    "vision_stale": "Làm mới crop bằng /vision-preview và mô tả lại.",
    "pulse_error": "Không cần làm gì ngay; nếu lặp lại thì xem log errors.",
    "browser_unavailable": "Mở Edge bằng remote-debugging-port=9222 rồi /br.",
    "browser_stale": "Chạy /br để lấy context mới.",
    "social_plan_blocked": "Xem blocked reasons; nếu là privacy/policy thì không ép chạy tiếp.",
    "social_intent_mismatch": "Dùng lệnh đúng dạng /social-draft-test <yêu cầu>.",
    "social_context_blocked": "Giảm context, chuyển topic rõ hơn, hoặc dùng L0/không kèm trang.",
    "social_route_blocked": "Xem blocked reasons của router trước khi thử lại.",
    "social_draft_model_failed": "Thử lại sau, hoặc chạy /recovery để xem debug model cuối.",
    "social_target_missing": "Mở đúng bài social và chạy /br, hoặc viết rõ nội dung cần reply.",
}

RECOVERY_SEVERITY = {
    "privacy_gate_blocked": "high",
    "gpt_reply_failed": "medium",
    "vision_model_failed": "medium",
    "vision_capture_failed": "medium",
    "browser_unavailable": "medium",
    "social_plan_blocked": "medium",
    "social_context_blocked": "medium",
    "social_route_blocked": "medium",
    "social_draft_model_failed": "medium",
    "vision_no_preview": "low",
    "vision_clip_too_small": "low",
    "vision_focus_unavailable": "low",
    "vision_stale": "low",
    "browser_stale": "low",
    "pulse_error": "low",
    "social_intent_mismatch": "low",
    "social_target_missing": "low",
}

RECOVERY_CLEAR_GROUPS = {
    "vision": [
        "vision_no_preview",
        "vision_model_failed",
        "vision_clip_too_small",
        "vision_focus_unavailable",
        "vision_capture_failed",
        "vision_stale",
    ],
    "browser": [
        "browser_unavailable",
        "browser_stale",
    ],
    "social": [
        "social_plan_blocked",
        "social_intent_mismatch",
        "social_context_blocked",
        "social_route_blocked",
        "social_draft_model_failed",
    ],
}


def recovery_message(kind, detail=None, cooldown=True, mode=None):
    now = time.time()
    message = recovery_text(kind, mode=mode, detail=detail)
    existing = _last_notice.get(kind)
    last_time = existing.get("last_at", 0) if isinstance(existing, dict) else float(existing or 0)
    if cooldown and now - last_time < RECOVERY_COOLDOWN_SECONDS:
        if isinstance(existing, dict):
            existing["suppressed"] = int(existing.get("suppressed", 0)) + 1
            _last_notice[kind] = existing
        else:
            _last_notice[kind] = {
                "last_at": last_time,
                "count": 1,
                "suppressed": 1,
                "detail": _trim_detail(detail),
            }
        return None

    count = int(existing.get("count", 0)) + 1 if isinstance(existing, dict) else 1
    suppressed = int(existing.get("suppressed", 0)) if isinstance(existing, dict) else 0
    _last_notice[kind] = {
        "last_at": now,
        "count": count,
        "suppressed": suppressed,
        "detail": _trim_detail(detail),
    }
    if detail:
        log_event("recovery", f"{kind}: {detail}")
    else:
        log_event("recovery", kind)
    return message


def recovery_known_kinds():
    kinds = set(RECOVERY_MESSAGES)
    kinds.update(RECOVERY_ADVICE)
    kinds.update(RECOVERY_SEVERITY)
    for mode_messages in RECOVERY_MODE_MESSAGES.values():
        kinds.update(mode_messages)
    return sorted(kinds)


def recovery_snapshot():
    now = time.time()
    rows = []
    for kind, record in _last_notice.items():
        if not isinstance(record, dict):
            record = {"last_at": float(record or 0), "count": 1, "suppressed": 0, "detail": None}
        last_at = float(record.get("last_at") or 0.0)
        rows.append({
            "kind": kind,
            "age": None if not last_at else max(0.0, now - last_at),
            "last_at": last_at,
            "count": int(record.get("count") or 0),
            "suppressed": int(record.get("suppressed") or 0),
            "detail": record.get("detail"),
            "severity": RECOVERY_SEVERITY.get(kind, "low"),
            "advice": RECOVERY_ADVICE.get(kind),
        })
    rows.sort(key=lambda row: row["last_at"], reverse=True)
    return {
        "cooldown_seconds": RECOVERY_COOLDOWN_SECONDS,
        "active": rows,
        "active_count": len(rows),
        "suppressed_total": sum(row["suppressed"] for row in rows),
        "known_kinds": recovery_known_kinds(),
        "clear_groups": {name: list(kinds) for name, kinds in RECOVERY_CLEAR_GROUPS.items()},
    }


def recovery_governor_config():
    known = recovery_known_kinds()
    return {
        "cooldown_seconds": RECOVERY_COOLDOWN_SECONDS,
        "known_kinds": known,
        "message_kinds": sorted(RECOVERY_MESSAGES),
        "advice_kinds": sorted(RECOVERY_ADVICE),
        "severity_kinds": sorted(RECOVERY_SEVERITY),
        "mode_kinds": sorted(RECOVERY_MODE_MESSAGES),
        "clear_groups": {name: list(kinds) for name, kinds in RECOVERY_CLEAR_GROUPS.items()},
    }


def recovery_dry_run(kind, detail=None, mode=None, last_age=None, cooldown=True):
    kind = str(kind or "").strip() or "unknown"
    message = recovery_text(kind, mode=mode, detail=detail)
    severity = RECOVERY_SEVERITY.get(kind, "low")
    advice = RECOVERY_ADVICE.get(kind)
    known = kind in recovery_known_kinds()
    would_emit = True
    reason = "ready"
    if cooldown and last_age is not None and last_age < RECOVERY_COOLDOWN_SECONDS:
        would_emit = False
        reason = "cooldown_suppressed"
    return {
        "kind": kind,
        "known": known,
        "severity": severity,
        "advice": advice,
        "message": message,
        "would_emit": would_emit,
        "reason": reason,
        "cooldown_seconds": RECOVERY_COOLDOWN_SECONDS,
        "last_age": last_age,
        "mode": mode or "chill",
        "detail": _trim_detail(detail),
    }


def recovery_text(kind, mode=None, detail=None):
    if kind == "social_target_missing":
        detail_text = str(detail or "").lower()
        if "browser_kind=" in detail_text and "browser_kind=social" not in detail_text:
            if any(kind_name in detail_text for kind_name in ["youtube", "video", "music"]):
                return "Tab hiện tại không phải bài social. Ba mở đúng tweet/bài X/Facebook rồi /br, hoặc gửi nội dung trực tiếp nha."
            return "Con chưa ở đúng tab social để reply. Ba mở đúng tweet/bài rồi /br, hoặc gửi nội dung trực tiếp nha."
        if "social_context_empty" in detail_text:
            return "Tab social đang mở nhưng chưa bắt được nội dung bài. Ba chạy /br lại hoặc scroll/focus vào bài chính nha."
    mode = (mode or "chill").strip().lower()
    if mode in RECOVERY_MODE_MESSAGES:
        message = RECOVERY_MODE_MESSAGES[mode].get(kind)
        if message:
            return message
    return RECOVERY_MESSAGES.get(kind, "Có lỗi nhẹ rồi Ba. Con sẽ fallback an toàn.")


def recovery_status_lines():
    now = time.time()
    lines = ["🧯 Recovery"]
    if not _last_notice:
        lines.append("  Last notices: none")
        return lines
    normalized = []
    for kind, record in _last_notice.items():
        if isinstance(record, dict):
            normalized.append((kind, record))
        else:
            normalized.append((kind, {"last_at": float(record or 0), "count": 1, "suppressed": 0, "detail": None}))
    for kind, record in sorted(normalized, key=lambda item: item[1].get("last_at", 0), reverse=True)[:8]:
        last = record.get("last_at", 0)
        age = max(0.0, now - last)
        severity = RECOVERY_SEVERITY.get(kind, "low")
        count = int(record.get("count", 1))
        suppressed = int(record.get("suppressed", 0))
        suffix = f" | count={count}"
        if suppressed:
            suffix += f" suppressed={suppressed}"
        lines.append(f"  {kind}: {age:.1f}s ago | severity={severity}{suffix}")
        detail = record.get("detail")
        if detail:
            lines.append(f"    detail: {detail}")
        advice = RECOVERY_ADVICE.get(kind)
        if advice:
            lines.append(f"    recovery: {advice}")
    return lines


def recovery_latest_summary(max_age_seconds=300):
    if not _last_notice:
        return None
    normalized = []
    for kind, record in _last_notice.items():
        if isinstance(record, dict):
            normalized.append((kind, record))
        else:
            normalized.append((kind, {"last_at": float(record or 0), "count": 1, "suppressed": 0, "detail": None}))
    if not normalized:
        return None
    kind, record = max(normalized, key=lambda item: item[1].get("last_at", 0))
    age = max(0.0, time.time() - record.get("last_at", 0))
    if age > max_age_seconds:
        return None
    severity = RECOVERY_SEVERITY.get(kind, "low")
    count = int(record.get("count", 1))
    suppressed = int(record.get("suppressed", 0))
    suffix = f"count={count}"
    if suppressed:
        suffix += f", suppressed={suppressed}"
    advice = RECOVERY_ADVICE.get(kind)
    if advice:
        return f"{kind} ({severity}, {age:.0f}s ago, {suffix}) | {advice}"
    return f"{kind} ({severity}, {age:.0f}s ago, {suffix})"


def recovery_clear():
    _last_notice.clear()
    log_event("recovery", "cleared")
    return ["🧯 Recovery", "  Status: cleared"]


def recovery_clear_kinds(kinds, reason="recovered"):
    removed = []
    for kind in kinds:
        if kind in _last_notice:
            _last_notice.pop(kind, None)
            removed.append(kind)
    if removed:
        log_event("recovery", f"{reason}: cleared {', '.join(removed)}")
    return removed


def recovery_clear_vision(reason="vision_recovered"):
    return recovery_clear_kinds(RECOVERY_CLEAR_GROUPS["vision"], reason=reason)


def recovery_clear_browser(reason="browser_recovered"):
    return recovery_clear_kinds(RECOVERY_CLEAR_GROUPS["browser"], reason=reason)


def recovery_clear_social(reason="social_recovered"):
    return recovery_clear_kinds(RECOVERY_CLEAR_GROUPS["social"], reason=reason)


def vision_recovery_kind(reason):
    reason = str(reason or "")
    if reason == "clip_too_small":
        return "vision_clip_too_small"
    if reason == "privacy_gate_blocked":
        return "privacy_gate_blocked"
    if reason.startswith("debug_port_unavailable") or reason in {"no_page", "no_websocket_debugger_url", "websocket_client_not_installed"}:
        return "browser_unavailable"
    if reason in {"no_focus_target", "empty_clip", "invalid_clip_json", "runtime_error", "clip_unavailable"}:
        return "vision_focus_unavailable"
    if reason.startswith("vision_exception"):
        return "vision_capture_failed"
    return None


def _trim_detail(detail, limit=160):
    if detail is None:
        return None
    text = " ".join(str(detail).split())
    if len(text) <= limit:
        return text
    return text[: limit - 3].rstrip() + "..."
