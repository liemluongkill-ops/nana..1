"""STAGE-9C-B: Owner-approved post-stream lessons.

Turns STAGE-9C-A1 recommendations into owner-approved lesson records.
This module writes only the dedicated ``memory["post_stream_lessons"]`` lane.
It never changes mood, intention, prompt config, stream policy, or live outputs.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import time
from typing import Any
import uuid

from nana.memory import memory, memory_lock, save_memory_async
from nana.runtime.post_stream_review import Recommendation, build_post_stream_review


PHASE = "STAGE-9C-B"
LOW_CONFIDENCE_APPROVAL_THRESHOLD = 0.50
CROSS_SESSION_APPROVAL_THRESHOLD = 0.70
DEFAULT_REVIEW_AFTER_DAYS = 30
DEFAULT_RELEVANCE_DECAY_DAYS = 45
DISMISS_RETENTION_DAYS = 30


@dataclass(frozen=True)
class LessonActionResult:
    ok: bool
    action: str
    reason: str
    recommendation_id: str = "none"
    lesson_id: str = "none"
    confidence: float = 0.0
    memory_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts if ts is not None else _now())))


def _short(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _recommendation_id(rec: Recommendation) -> str:
    payload = "|".join(
        [
            rec.type,
            rec.insight,
            rec.metric,
            str(rec.occurrences),
            str(rec.evidence_count),
            "\n".join(rec.evidence_snippets),
        ]
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:10]


def _lesson_id(source_recommendation_id: str) -> str:
    return f"lesson-{source_recommendation_id}-{uuid.uuid4().hex[:6]}"


def _default_store() -> dict[str, Any]:
    return {
        "phase": PHASE,
        "lessons": [],
        "dismissed": [],
        "audit_log": [],
    }


def _get_store_unlocked() -> dict[str, Any]:
    store = memory.setdefault("post_stream_lessons", _default_store())
    if not isinstance(store, dict):
        store = _default_store()
        memory["post_stream_lessons"] = store
    store.setdefault("phase", PHASE)
    store.setdefault("lessons", [])
    store.setdefault("dismissed", [])
    store.setdefault("audit_log", [])
    return store


def _recommendation_payload(rec: Recommendation) -> dict[str, Any]:
    rid = _recommendation_id(rec)
    return {
        "recommendation_id": rid,
        "type": rec.type,
        "insight": rec.insight,
        "confidence": rec.confidence,
        "metric": rec.metric,
        "occurrences": rec.occurrences,
        "evidence_count": rec.evidence_count,
        "evidence_sources": list(rec.evidence_sources),
        "evidence_snippets": list(rec.evidence_snippets),
        "suggested_adjustment": rec.suggested_adjustment,
        "correlation_warning": rec.correlation_warning,
        "correlated_with": list(rec.correlated_with),
        "auto_apply": False,
    }


def list_recommendation_payloads() -> list[dict[str, Any]]:
    review = build_post_stream_review()
    return [_recommendation_payload(rec) for rec in review.recommendations]


def _find_recommendation(recommendation_id: str) -> dict[str, Any] | None:
    needle = str(recommendation_id or "").strip().lower()
    if not needle:
        return None
    for payload in list_recommendation_payloads():
        rid = payload["recommendation_id"].lower()
        if rid == needle or rid.startswith(needle):
            return payload
    return None


def _has_active_lesson(store: dict[str, Any], recommendation_id: str) -> bool:
    for lesson in store.get("lessons", []):
        if lesson.get("source_recommendation_id") == recommendation_id and not lesson.get("revoked"):
            return True
    return False


def _is_rejected(store: dict[str, Any], recommendation_id: str) -> bool:
    for item in store.get("dismissed", []):
        if item.get("source_recommendation_id") == recommendation_id and not item.get("revoked"):
            return True
    return False


def _append_audit(store: dict[str, Any], *, action: str, recommendation_id: str, lesson_id: str = "none", reason: str = "") -> None:
    audit = list(store.get("audit_log", []))
    audit.append(
        {
            "audit_id": f"audit-{uuid.uuid4().hex[:10]}",
            "phase": PHASE,
            "action": action,
            "source_recommendation_id": recommendation_id,
            "lesson_id": lesson_id,
            "reason": reason,
            "created_at": _now(),
            "created_at_text": _iso(),
            "memory_lane": "post_stream_lessons",
        }
    )
    store["audit_log"] = audit[-200:]


def approve_recommendation(recommendation_id: str, *, override_low_confidence: bool = False) -> LessonActionResult:
    recommendation = _find_recommendation(recommendation_id)
    if not recommendation:
        return LessonActionResult(False, "approve", "recommendation_not_found", recommendation_id=str(recommendation_id or "none"))

    rid = recommendation["recommendation_id"]
    confidence = float(recommendation.get("confidence") or 0.0)
    if confidence < LOW_CONFIDENCE_APPROVAL_THRESHOLD and not override_low_confidence:
        return LessonActionResult(False, "approve", "low_confidence_requires_override", rid, confidence=confidence)
    if recommendation.get("type") == "cross_session" and confidence < CROSS_SESSION_APPROVAL_THRESHOLD:
        return LessonActionResult(False, "approve", "cross_session_confidence_too_low", rid, confidence=confidence)

    lesson_id = _lesson_id(rid)
    now = _now()
    lesson = {
        "lesson_id": lesson_id,
        "source_recommendation_id": rid,
        "type": recommendation.get("type"),
        "insight": recommendation.get("insight"),
        "confidence": confidence,
        "evidence_count": int(recommendation.get("evidence_count") or 0),
        "occurrences": int(recommendation.get("occurrences") or 0),
        "evidence_snippets": list(recommendation.get("evidence_snippets") or []),
        "suggested_adjustment": recommendation.get("suggested_adjustment"),
        "metric": recommendation.get("metric"),
        "applied_at": now,
        "applied_at_text": _iso(now),
        "review_after": now + DEFAULT_REVIEW_AFTER_DAYS * 86400,
        "review_after_text": _iso(now + DEFAULT_REVIEW_AFTER_DAYS * 86400),
        "relevance_decay_days": DEFAULT_RELEVANCE_DECAY_DAYS,
        "superseded_by": None,
        "revoked": False,
        "revoked_at": None,
        "revoke_reason": None,
        "auto_apply": False,
        "behavior_config_write": False,
        "memory_lane": "post_stream_lessons",
    }

    with memory_lock:
        store = _get_store_unlocked()
        if _has_active_lesson(store, rid):
            return LessonActionResult(False, "approve", "already_approved", rid, confidence=confidence)
        store["lessons"] = list(store.get("lessons", [])) + [lesson]
        _append_audit(store, action="approved", recommendation_id=rid, lesson_id=lesson_id)
    save_memory_async()
    return LessonActionResult(True, "approve", "lesson_recorded", rid, lesson_id, confidence, memory_write=True)


def reject_recommendation(recommendation_id: str, *, reason: str = "owner_rejected") -> LessonActionResult:
    recommendation = _find_recommendation(recommendation_id)
    if not recommendation:
        return LessonActionResult(False, "reject", "recommendation_not_found", recommendation_id=str(recommendation_id or "none"))
    rid = recommendation["recommendation_id"]
    now = _now()
    dismissed = {
        "dismiss_id": f"dismiss-{uuid.uuid4().hex[:8]}",
        "source_recommendation_id": rid,
        "type": recommendation.get("type"),
        "insight": recommendation.get("insight"),
        "confidence": recommendation.get("confidence"),
        "dismissed_at": now,
        "dismissed_at_text": _iso(now),
        "keep_until": now + DISMISS_RETENTION_DAYS * 86400,
        "keep_until_text": _iso(now + DISMISS_RETENTION_DAYS * 86400),
        "reason": reason or "owner_rejected",
        "revoked": False,
        "memory_lane": "post_stream_lessons.dismissed",
    }
    with memory_lock:
        store = _get_store_unlocked()
        if _has_active_lesson(store, rid):
            return LessonActionResult(False, "reject", "already_approved", rid, confidence=float(recommendation.get("confidence") or 0.0))
        if _is_rejected(store, rid):
            return LessonActionResult(False, "reject", "already_rejected", rid, confidence=float(recommendation.get("confidence") or 0.0))
        store["dismissed"] = list(store.get("dismissed", [])) + [dismissed]
        _append_audit(store, action="rejected", recommendation_id=rid, reason=reason or "owner_rejected")
    save_memory_async()
    return LessonActionResult(True, "reject", "recommendation_dismissed", rid, confidence=float(recommendation.get("confidence") or 0.0), memory_write=True)


def revoke_lesson(lesson_id: str, *, reason: str = "owner_undo") -> LessonActionResult:
    needle = str(lesson_id or "").strip().lower()
    if not needle:
        return LessonActionResult(False, "undo", "missing_lesson_id")
    with memory_lock:
        store = _get_store_unlocked()
        lessons = list(store.get("lessons", []))
        match = None
        for lesson in lessons:
            lid = str(lesson.get("lesson_id") or "")
            if lid.lower() == needle or lid.lower().startswith(needle):
                match = lesson
                break
        if not match:
            return LessonActionResult(False, "undo", "lesson_not_found", lesson_id=lesson_id)
        if match.get("revoked"):
            return LessonActionResult(
                False,
                "undo",
                "already_revoked",
                recommendation_id=str(match.get("source_recommendation_id") or "none"),
                lesson_id=str(match.get("lesson_id") or lesson_id),
                confidence=float(match.get("confidence") or 0.0),
            )
        match["revoked"] = True
        match["revoked_at"] = _now()
        match["revoked_at_text"] = _iso(match["revoked_at"])
        match["revoke_reason"] = reason or "owner_undo"
        _append_audit(
            store,
            action="revoked",
            recommendation_id=str(match.get("source_recommendation_id") or "none"),
            lesson_id=str(match.get("lesson_id") or lesson_id),
            reason=reason or "owner_undo",
        )
    save_memory_async()
    return LessonActionResult(
        True,
        "undo",
        "lesson_revoked",
        recommendation_id=str(match.get("source_recommendation_id") or "none"),
        lesson_id=str(match.get("lesson_id") or lesson_id),
        confidence=float(match.get("confidence") or 0.0),
        memory_write=True,
    )


def lesson_snapshot() -> dict[str, Any]:
    recommendations = list_recommendation_payloads()
    with memory_lock:
        store = dict(_get_store_unlocked())
        lessons = [dict(item) for item in store.get("lessons", [])]
        dismissed = [dict(item) for item in store.get("dismissed", [])]
        audit_log = [dict(item) for item in store.get("audit_log", [])]
    active_lessons = [item for item in lessons if not item.get("revoked")]
    revoked_lessons = [item for item in lessons if item.get("revoked")]
    return {
        "phase": PHASE,
        "recommendations": recommendations,
        "recommendation_count": len(recommendations),
        "lessons": lessons,
        "active_lessons": active_lessons,
        "revoked_lessons": revoked_lessons,
        "dismissed": dismissed,
        "audit_log": audit_log,
        "read_only_status": True,
        "can_act": False,
        "auto_apply": False,
        "behavior_config_write": False,
    }


def _format_recommendation(item: dict[str, Any]) -> str:
    return (
        f"[{item.get('recommendation_id')}] {item.get('type')} | "
        f"confidence={float(item.get('confidence') or 0.0):.2f} | "
        f"occurrences={item.get('occurrences')} | unique_evidence={item.get('evidence_count')} | "
        f"{_short(item.get('insight'), 110)}"
    )


def recommendation_lines() -> list[str]:
    snap = lesson_snapshot()
    lines = [
        "🧾 Post-stream Recommendations (STAGE-9C-B)",
        "  Mode: approval_queue | read_only=True | auto_apply=False",
        (
            "  Counts: "
            f"pending={snap['recommendation_count']} | active_lessons={len(snap['active_lessons'])} | "
            f"rejected={len(snap['dismissed'])} | revoked={len(snap['revoked_lessons'])}"
        ),
        "  Commands: /post-stream-approve <id> [--override-low-confidence] | /post-stream-reject <id> | /post-stream-undo <lesson_id> | /lesson-history",
    ]
    if not snap["recommendations"]:
        lines.append("  Recommendations: none")
    else:
        lines.append("  Pending recommendations:")
        for item in snap["recommendations"][:8]:
            lines.append(f"    - {_format_recommendation(item)}")
            if item.get("correlation_warning"):
                lines.append(f"      correlation: {', '.join(item.get('correlated_with') or [])}")
    lines.append("  Safety: owner approval required | lesson lane only | no mood/intention/config write")
    return lines


def lesson_status_lines() -> list[str]:
    snap = lesson_snapshot()
    stale_count = len(_stale_lessons(snap["active_lessons"]))
    return [
        "📚 Post-stream Lessons (STAGE-9C-B)",
        "  Mode: owner_approved_lessons | can_act=False | auto_apply=False",
        (
            "  Counts: "
            f"recommendations={snap['recommendation_count']} | active={len(snap['active_lessons'])} | "
            f"revoked={len(snap['revoked_lessons'])} | rejected={len(snap['dismissed'])} | "
            f"audit={len(snap['audit_log'])} | stale={stale_count}"
        ),
        "  Storage: memory.post_stream_lessons only | no mood/intention/config write",
        "  Commands: /post-stream-recommendations | /post-stream-approve <id> | /post-stream-reject <id> | /post-stream-undo <lesson_id> | /lesson-history | /lesson-stale",
        "  Safety: explicit owner command required | no auto-apply | no live action",
    ]


def approve_lines(raw_args: str) -> list[str]:
    parts = str(raw_args or "").split()
    rec_id = parts[0] if parts else ""
    override = any(part == "--override-low-confidence" for part in parts[1:])
    result = approve_recommendation(rec_id, override_low_confidence=override)
    return [
        "📚 Lesson Approve (STAGE-9C-B)",
        f"  OK: {result.ok} | action={result.action} | reason={result.reason}",
        f"  Recommendation: {result.recommendation_id}",
        f"  Lesson: {result.lesson_id}",
        f"  Confidence: {result.confidence:.2f} | memory_write={result.memory_write}",
        "  Safety: lesson lane only | no mood/intention/config write | no live action",
    ]


def reject_lines(raw_args: str) -> list[str]:
    parts = str(raw_args or "").split(maxsplit=1)
    rec_id = parts[0] if parts else ""
    reason = parts[1].strip() if len(parts) > 1 else "owner_rejected"
    result = reject_recommendation(rec_id, reason=reason)
    return [
        "📚 Recommendation Reject (STAGE-9C-B)",
        f"  OK: {result.ok} | action={result.action} | reason={result.reason}",
        f"  Recommendation: {result.recommendation_id}",
        f"  Confidence: {result.confidence:.2f} | memory_write={result.memory_write}",
        "  Safety: dismissed audit only | no mood/intention/config write | no live action",
    ]


def undo_lines(raw_args: str) -> list[str]:
    parts = str(raw_args or "").split(maxsplit=1)
    lesson_id = parts[0] if parts else ""
    reason = parts[1].strip() if len(parts) > 1 else "owner_undo"
    result = revoke_lesson(lesson_id, reason=reason)
    return [
        "↩️ Lesson Undo/Revoke (STAGE-9C-B)",
        f"  OK: {result.ok} | action={result.action} | reason={result.reason}",
        f"  Recommendation: {result.recommendation_id}",
        f"  Lesson: {result.lesson_id}",
        f"  Memory write: {result.memory_write}",
        "  Safety: audit retained | revoked=True | no hard delete | no config write",
    ]


def lesson_history_lines(limit: int = 12) -> list[str]:
    snap = lesson_snapshot()
    lines = [
        "📚 Lesson History (STAGE-9C-B)",
        f"  Active lessons: {len(snap['active_lessons'])} | revoked={len(snap['revoked_lessons'])} | rejected={len(snap['dismissed'])}",
    ]
    if snap["active_lessons"]:
        lines.append("  Active:")
        for lesson in snap["active_lessons"][:limit]:
            lines.append(
                f"    - {lesson.get('lesson_id')} | {lesson.get('type')} | review_after={lesson.get('review_after_text')} | {_short(lesson.get('insight'), 110)}"
            )
    if snap["revoked_lessons"]:
        lines.append("  Revoked:")
        for lesson in snap["revoked_lessons"][:limit]:
            lines.append(
                f"    - {lesson.get('lesson_id')} | reason={lesson.get('revoke_reason')} | revoked_at={lesson.get('revoked_at_text')}"
            )
    if snap["dismissed"]:
        lines.append("  Rejected:")
        for item in snap["dismissed"][:limit]:
            lines.append(
                f"    - {item.get('source_recommendation_id')} | {item.get('type')} | keep_until={item.get('keep_until_text')} | reason={item.get('reason')}"
            )
    lines.append(f"  Audit rows: {len(snap['audit_log'])}")
    return lines


def _stale_lessons(active_lessons: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = _now()
    return [lesson for lesson in active_lessons if float(lesson.get("review_after") or 0.0) <= now]


def lesson_stale_lines() -> list[str]:
    snap = lesson_snapshot()
    stale = _stale_lessons(snap["active_lessons"])
    lines = [
        "🕰️ Lesson Review Queue (STAGE-9C-B)",
        f"  Stale/review-due: {len(stale)} | active={len(snap['active_lessons'])}",
    ]
    if stale:
        for lesson in stale[:12]:
            lines.append(
                f"    - {lesson.get('lesson_id')} | review_after={lesson.get('review_after_text')} | {_short(lesson.get('insight'), 120)}"
            )
    else:
        lines.append("  Items: none")
    lines.append("  Safety: read-only; use /post-stream-undo <lesson_id> to revoke.")
    return lines
