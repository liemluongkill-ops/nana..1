"""STAGE-9C-A1: Recommendation-only post-stream review.

Consumes STAGE-9C-A0 metrics/events and emits evidence-backed recommendations.
This module never writes memory/config, never auto-applies behavior, never calls
LLMs, and never performs live actions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
import re
from typing import Any

from nana.runtime.session_review_adapter import (
    DEFAULT_LIMIT,
    DEFAULT_OUTBOX_SENT_DIR,
    DEFAULT_REPLIES_DIR,
    OutboxEvent,
    SessionEvent,
    SessionReviewDataset,
    SessionReviewMetrics,
    build_review_metrics,
)


PHASE = "STAGE-9C-A1"
MIN_PATTERN_OCCURRENCES = 3
CORRELATION_OVERLAP_THRESHOLD = 0.50


def _short(value: Any, limit: int = 150) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _strip_stage_direction(text: str) -> tuple[str, bool]:
    """Strip leading parenthetical/emote stage directions for owner-readable evidence."""
    original = str(text or "").strip()
    cleaned = original
    stripped = False
    while True:
        next_text = re.sub(r"^\s*(?:\([^()\n]{0,240}\)|\*[^*\n]{0,220}\*)\s*", "", cleaned, count=1).strip()
        if next_text == cleaned:
            break
        stripped = True
        cleaned = next_text
    return cleaned or original, stripped


def _ratio(value: float | None) -> float:
    return 0.0 if value is None else float(value)


def _confidence_from_count(count: int, *, strong_at: int = 8) -> float:
    if count < MIN_PATTERN_OCCURRENCES:
        return 0.0
    if count >= strong_at:
        return 0.72
    return 0.42


@dataclass(frozen=True)
class Recommendation:
    type: str
    insight: str
    confidence: float
    evidence_snippets: tuple[str, ...]
    suggested_adjustment: str
    metric: str
    occurrences: int
    evidence_count: int
    evidence_sources: tuple[str, ...] = field(default_factory=tuple)
    correlated_with: tuple[str, ...] = field(default_factory=tuple)
    correlation_warning: bool = False
    auto_apply: bool = False
    read_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PostStreamReview:
    phase: str
    generated: bool
    reason: str
    recommendations: tuple[Recommendation, ...] = field(default_factory=tuple)
    observations: tuple[str, ...] = field(default_factory=tuple)
    metrics: dict[str, Any] = field(default_factory=dict)
    read_only: bool = True
    can_act: bool = False
    auto_apply: bool = False
    memory_write: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["recommendations"] = [item.to_dict() for item in self.recommendations]
        return data


def _tagged_evidence_from_reply(event: SessionEvent) -> tuple[str, str] | None:
    text = _short(event.reply_text_preview, 180)
    if not text or text == "unknown":
        return None
    cleaned, stripped = _strip_stage_direction(text)
    tags = ["reply"]
    if stripped:
        tags.append("stage_dir_stripped")
    return f"[{']['.join(tags)}] {_short(cleaned, 140)}", _short(cleaned, 180).lower()


def _tagged_evidence_from_outbox(event: OutboxEvent) -> tuple[str, str] | None:
    text = _short(event.text_preview, 180)
    if not text or text == "unknown":
        return None
    cleaned, stripped = _strip_stage_direction(text)
    tags = ["outbox"]
    if stripped:
        tags.append("stage_dir_stripped")
    return f"[{']['.join(tags)}] {_short(cleaned, 140)}", _short(cleaned, 180).lower()


def _reply_evidence_priority(event: SessionEvent) -> tuple[int, float, int]:
    """Prefer evidence that reflects organic chat over probes/rehearsal turns."""
    reasons = set(event.test_noise_reasons or ())
    text = str(event.reply_text_preview or "").lower()
    penalty = 0
    if event.test_noise_kind not in {"none", "normal"}:
        penalty += 60
    if event.learning_weight < 1.0:
        penalty += 40
    if "quiet_room_probe" in reasons or event.social_event_type == "quiet_room_probe":
        penalty += 25
    if event.social_event_type in {"identity_challenge", "service_role_request"}:
        penalty += 10
    if event.public_quality_violation_count or event.stage_identity_violation_count or event.stage_identity_firewalled:
        penalty += 8
    if "phòng im" in text:
        penalty += 4
    generic_patterns = (
        r"\bnana\s+thấy\s+tin\s+nhắn\b",
        r"\bnana\s+(?:đang\s+)?nghe\b",
        r"\bcứ\s+nói\s+tiếp\b",
        r"\bkết\s+nối\s+ổn\s+định\b",
        r"\bcó\s+gì\s+cần\s+hỗ\s+trợ\b",
        r"\bquầy\s+hỗ\s+trợ\b",
        r"\bhệ\s+thống\b",
        r"\bkiểm\s+tra\b",
        r"\bbot\s+discord\b",
        r"\bgpt\b",
        r"\bmodel\b",
        r"^\s*chào\b",
    )
    if any(re.search(pattern, text) for pattern in generic_patterns):
        penalty += 30
    quiet_room_patterns = (
        r"\bphòng\s+(?:đang\s+)?(?:im|yên|vắng|lặng)\b",
        r"\byên\s+quá\b",
        r"\bim\s+lặng\b",
        r"\bhơi\s+vắng\b",
        r"\bhơi\s+lặng\b",
        r"\bném\s+mồi\b",
        r"\brải\s+một\s+mồi\b",
    )
    if any(re.search(pattern, text) for pattern in quiet_room_patterns):
        penalty += 22
    repeat_probe_patterns = (
        r"\bcùng\s+một\s+câu\b",
        r"\blặp\s+lại\b",
        r"\bpattern\b",
        r"\bbài\s+kiểm\s+tra\b",
        r"\bmồi\s+cũ\b",
        r"\bđủ\s+điểm\s+danh\b",
    )
    if any(re.search(pattern, text) for pattern in repeat_probe_patterns):
        penalty += 28
    return (penalty, -float(event.learning_weight), len(str(event.reply_text_preview or "")))


def _event_texts(events: list[SessionEvent], *, limit: int = 3) -> tuple[tuple[str, ...], int, tuple[str, ...], frozenset[str]]:
    snippets: list[str] = []
    keys: list[str] = []
    ordered_events = sorted(events, key=_reply_evidence_priority)
    for event in ordered_events:
        tagged = _tagged_evidence_from_reply(event)
        if not tagged:
            continue
        text, key = tagged
        if key not in keys:
            snippets.append(text)
            keys.append(key)
    return tuple(snippets[:limit]), len(keys), ("reply",), frozenset(keys)


def _outbox_texts(events: list[OutboxEvent], *, limit: int = 3) -> tuple[tuple[str, ...], int, tuple[str, ...], frozenset[str]]:
    snippets: list[str] = []
    keys: list[str] = []
    for event in events:
        tagged = _tagged_evidence_from_outbox(event)
        if not tagged:
            continue
        text, key = tagged
        if key not in keys:
            snippets.append(text)
            keys.append(key)
    return tuple(snippets[:limit]), len(keys), ("outbox",), frozenset(keys)


def _add_recommendation(
    target: list[Recommendation],
    *,
    type: str,
    insight: str,
    confidence: float,
    evidence: tuple[str, ...],
    evidence_count: int,
    evidence_sources: tuple[str, ...],
    evidence_keys: frozenset[str],
    suggested_adjustment: str,
    metric: str,
    occurrences: int,
) -> None:
    if confidence <= 0.0 or occurrences < MIN_PATTERN_OCCURRENCES or not evidence:
        return
    recommendation = Recommendation(
        type=type,
        insight=insight,
        confidence=round(min(0.95, max(0.0, confidence)), 2),
        evidence_snippets=evidence,
        suggested_adjustment=suggested_adjustment,
        metric=metric,
        occurrences=occurrences,
        evidence_count=evidence_count,
        evidence_sources=tuple(sorted(set(evidence_sources))),
        auto_apply=False,
    )
    object.__setattr__(recommendation, "_evidence_keys", evidence_keys)
    target.append(recommendation)


def _with_correlations(recommendations: list[Recommendation]) -> tuple[Recommendation, ...]:
    if not recommendations:
        return ()
    key_map: dict[str, frozenset[str]] = {
        rec.type: getattr(rec, "_evidence_keys", frozenset()) for rec in recommendations
    }
    correlated: list[Recommendation] = []
    for rec in recommendations:
        own_keys = key_map.get(rec.type, frozenset())
        related: list[str] = []
        if own_keys:
            for other in recommendations:
                if other.type == rec.type:
                    continue
                other_keys = key_map.get(other.type, frozenset())
                if not other_keys:
                    continue
                overlap = len(own_keys & other_keys) / max(1, min(len(own_keys), len(other_keys)))
                if overlap > CORRELATION_OVERLAP_THRESHOLD:
                    related.append(other.type)
        correlated.append(
            Recommendation(
                type=rec.type,
                insight=rec.insight,
                confidence=rec.confidence,
                evidence_snippets=rec.evidence_snippets,
                suggested_adjustment=rec.suggested_adjustment,
                metric=rec.metric,
                occurrences=rec.occurrences,
                evidence_count=rec.evidence_count,
                evidence_sources=rec.evidence_sources,
                correlated_with=tuple(sorted(set(related))),
                correlation_warning=bool(related),
                auto_apply=rec.auto_apply,
                read_only=rec.read_only,
            )
        )
    return tuple(correlated)


def generate_recommendations(
    dataset: SessionReviewDataset,
    metrics: SessionReviewMetrics,
) -> tuple[tuple[Recommendation, ...], tuple[str, ...]]:
    reply_events = list(dataset.reply_events)
    learning_events = [event for event in reply_events if event.learning_eligible]
    outbox_events = list(dataset.outbox_events)
    recs: list[Recommendation] = []
    observations: list[str] = []
    learning_total = len(learning_events)
    learning_action_counts = Counter(event.social_action for event in learning_events)
    learning_social_event_counts = Counter(event.social_event_type for event in learning_events)
    learning_avatar_event_counts = Counter(event.avatar_event for event in learning_events)

    if not reply_events and not outbox_events:
        return (), ("No structured session artifacts found.",)

    if metrics.learning_excluded_events:
        observations.append(
            f"{metrics.learning_excluded_events} public reply events were marked as test/rehearsal noise and excluded from recommendations."
        )
    if metrics.learning_reduced_events:
        observations.append(
            f"{metrics.learning_reduced_events} public reply events kept reduced learning weight."
        )

    unknown_actions = metrics.action_counts.get("unknown", 0)
    if unknown_actions:
        observations.append(
            f"{unknown_actions} reply events use legacy/unknown social action fields; confidence should stay conservative."
        )

    if metrics.corrupt_files:
        observations.append(f"{metrics.corrupt_files} corrupt files were skipped.")
    if metrics.missing_field_files:
        observations.append(f"{metrics.missing_field_files} files have missing structured fields.")
    if metrics.reply_eval_issue_counts:
        issue_summary = ", ".join(
            f"{key}={value}"
            for key, value in sorted(metrics.reply_eval_issue_counts.items(), key=lambda item: (-item[1], item[0]))[:4]
        )
        observations.append(f"Public reply evaluator flagged style issues: {issue_summary}.")

    ack_count = learning_action_counts.get("ack_only", 0)
    ack_ratio = (ack_count / learning_total) if learning_total else 0.0
    ack_events = [event for event in learning_events if event.social_action == "ack_only"]
    if ack_count >= MIN_PATTERN_OCCURRENCES:
        confidence = _confidence_from_count(ack_count)
        if ack_ratio >= 0.15:
            confidence += 0.08
        evidence, evidence_count, evidence_sources, evidence_keys = _event_texts(ack_events)
        _add_recommendation(
            recs,
            type="interaction_timing",
            insight="Light reactions are appearing often enough to justify keeping short acknowledgements in the stage rhythm.",
            confidence=confidence,
            evidence=evidence,
            evidence_count=evidence_count,
            evidence_sources=evidence_sources,
            evidence_keys=evidence_keys,
            suggested_adjustment="Keep emoji/sticker replies short and playful; avoid escalating every light reaction into a full model reply.",
            metric=f"learnable_ack_only_count={ack_count}, learnable_ack_ratio={round(ack_ratio, 3)}",
            occurrences=ack_count,
        )

    full_count = learning_action_counts.get("full_reply", 0)
    full_ratio = (full_count / learning_total) if learning_total else 0.0
    full_events = [event for event in learning_events if event.social_action == "full_reply"]
    if full_count >= MIN_PATTERN_OCCURRENCES and full_ratio >= 0.35:
        evidence, evidence_count, evidence_sources, evidence_keys = _event_texts(full_events)
        _add_recommendation(
            recs,
            type="reply_density",
            insight="Full replies are a major part of the public-stage session.",
            confidence=_confidence_from_count(full_count, strong_at=12),
            evidence=evidence,
            evidence_count=evidence_count,
            evidence_sources=evidence_sources,
            evidence_keys=evidence_keys,
            suggested_adjustment="For busy public rooms, keep full replies concise and reserve longer texture for quieter moments.",
            metric=f"learnable_full_reply_count={full_count}, learnable_full_reply_ratio={round(full_ratio, 3)}",
            occurrences=full_count,
        )

    emoji_count = (
        learning_social_event_counts.get("emoji_only", 0)
        + learning_social_event_counts.get("sticker", 0)
        + learning_avatar_event_counts.get("emoji_only", 0)
        + learning_avatar_event_counts.get("sticker", 0)
    )
    emoji_count = min(learning_total, emoji_count)
    emoji_events = [
        event
        for event in learning_events
        if event.social_event_type in {"emoji_only", "sticker"}
        or event.avatar_event in {"emoji_only", "sticker"}
    ]
    emoji_ratio = (emoji_count / learning_total) if learning_total else 0.0
    if emoji_count >= MIN_PATTERN_OCCURRENCES:
        evidence, evidence_count, evidence_sources, evidence_keys = _event_texts(emoji_events)
        _add_recommendation(
            recs,
            type="avatar_reaction",
            insight="Emoji/sticker activity is visible enough to support avatar reaction planning.",
            confidence=_confidence_from_count(emoji_count),
            evidence=evidence,
            evidence_count=evidence_count,
            evidence_sources=evidence_sources,
            evidence_keys=evidence_keys,
            suggested_adjustment="Let avatar reaction planning stay responsive to emoji/sticker bursts, while keeping text replies lightweight.",
            metric=f"learnable_emoji_sticker_count={emoji_count}, learnable_emoji_sticker_ratio={round(emoji_ratio, 3)}",
            occurrences=emoji_count,
        )

    quality_issues = (
        sum(event.public_quality_violation_count for event in learning_events)
        + sum(event.stage_identity_violation_count for event in learning_events)
        + sum(1 for event in learning_events if event.stage_identity_firewalled)
    )
    if quality_issues >= MIN_PATTERN_OCCURRENCES:
        issue_events = [
            event
            for event in learning_events
            if event.public_quality_violation_count
            or event.stage_identity_violation_count
            or event.stage_identity_firewalled
        ]
        evidence, evidence_count, evidence_sources, evidence_keys = _event_texts(issue_events)
        _add_recommendation(
            recs,
            type="persona_guard",
            insight="Public quality or stage identity guards triggered repeatedly during the session.",
            confidence=_confidence_from_count(quality_issues),
            evidence=evidence,
            evidence_count=evidence_count,
            evidence_sources=evidence_sources,
            evidence_keys=evidence_keys,
            suggested_adjustment="Review the prompt/guard examples before raising automation; repeated guard hits mean Nana's public voice still needs tightening.",
            metric=f"quality_stage_issues={quality_issues}",
            occurrences=quality_issues,
        )
    elif quality_issues:
        observations.append(f"{quality_issues} guard issue observed once or twice; not enough for a recommendation.")

    reply_eval_issues = sum(event.reply_eval_issue_count for event in learning_events)
    if reply_eval_issues >= MIN_PATTERN_OCCURRENCES:
        eval_issue_events = [event for event in learning_events if event.reply_eval_issue_count]
        evidence, evidence_count, evidence_sources, evidence_keys = _event_texts(eval_issue_events)
        _add_recommendation(
            recs,
            type="public_reply_quality",
            insight="Final public replies repeatedly carried style-evaluator warnings after safety and fluency polish.",
            confidence=_confidence_from_count(reply_eval_issues),
            evidence=evidence,
            evidence_count=evidence_count,
            evidence_sources=evidence_sources,
            evidence_keys=evidence_keys,
            suggested_adjustment="Review recurring public-reply evaluator flags before making the public lane more autonomous.",
            metric=f"public_reply_eval_issues={reply_eval_issues}",
            occurrences=reply_eval_issues,
        )
    elif reply_eval_issues:
        observations.append(f"{reply_eval_issues} public reply evaluator issue observed once or twice; not enough for a recommendation.")

    outbox_count = len(outbox_events)
    if outbox_count >= MIN_PATTERN_OCCURRENCES:
        evidence, evidence_count, evidence_sources, evidence_keys = _outbox_texts(outbox_events)
        _add_recommendation(
            recs,
            type="proactive_starter",
            insight="Controlled starter sends occurred often enough to review proactive topic timing.",
            confidence=_confidence_from_count(outbox_count),
            evidence=evidence,
            evidence_count=evidence_count,
            evidence_sources=evidence_sources,
            evidence_keys=evidence_keys,
            suggested_adjustment="Keep starter auto-lite gated by stream policy and cooldown; use post-stream review to tune starter phrasing before increasing quota.",
            metric=f"outbox_sent_count={outbox_count}",
            occurrences=outbox_count,
        )
    elif outbox_count:
        observations.append(f"{outbox_count} starter outbox event(s) found; below recommendation threshold.")

    if not recs:
        observations.append("No pattern reached the 3-occurrence recommendation threshold.")

    recs.sort(key=lambda item: (-item.confidence, item.type))
    correlated_recs = _with_correlations(recs)
    correlated_count = sum(1 for rec in correlated_recs if rec.correlation_warning)
    if correlated_count:
        observations.append(
            f"{correlated_count} recommendation(s) share more than 50% evidence with another recommendation; review together."
        )
    return correlated_recs, tuple(observations)


def build_post_stream_review(
    *,
    replies_dir: Path | None = None,
    outbox_sent_dir: Path | None = None,
    limit: int = DEFAULT_LIMIT,
) -> PostStreamReview:
    dataset, metrics = build_review_metrics(
        replies_dir=replies_dir or DEFAULT_REPLIES_DIR,
        outbox_sent_dir=outbox_sent_dir or DEFAULT_OUTBOX_SENT_DIR,
        limit=limit,
    )
    recommendations, observations = generate_recommendations(dataset, metrics)
    reason = "recommendations_ready" if recommendations else "no_recommendation_threshold_met"
    return PostStreamReview(
        phase=PHASE,
        generated=True,
        reason=reason,
        recommendations=recommendations,
        observations=observations,
        metrics={
            "reply_events": metrics.reply_events,
            "outbox_events": metrics.outbox_events,
            "corrupt_files": metrics.corrupt_files,
            "missing_field_files": metrics.missing_field_files,
            "learning_eligible_events": metrics.learning_eligible_events,
            "learning_excluded_events": metrics.learning_excluded_events,
            "learning_reduced_events": metrics.learning_reduced_events,
            "average_learning_weight": metrics.average_learning_weight,
            "test_noise_counts": metrics.test_noise_counts,
            "full_reply_ratio": metrics.full_reply_ratio,
            "ack_ratio": metrics.ack_ratio,
            "skip_ratio": metrics.skip_ratio,
            "emoji_sticker_ratio": metrics.emoji_sticker_ratio,
            "quality_violation_count": metrics.quality_violation_count,
            "stage_identity_violation_count": metrics.stage_identity_violation_count,
            "stage_firewall_count": metrics.stage_firewall_count,
        },
    )


def post_stream_review_status_lines() -> list[str]:
    review = build_post_stream_review()
    metrics = review.metrics
    return [
        "🧾 Post-stream Review (STAGE-9C-A1)",
        "  Mode: recommendation_only | read_only=True | can_act=False | auto_apply=False",
        (
            "  Inputs: "
            f"reply_events={metrics.get('reply_events')} | outbox_events={metrics.get('outbox_events')} | "
            f"corrupt={metrics.get('corrupt_files')} | missing_fields={metrics.get('missing_field_files')}"
        ),
        (
            "  Learning filter: "
            f"eligible={metrics.get('learning_eligible_events')} | "
            f"excluded={metrics.get('learning_excluded_events')} | "
            f"reduced={metrics.get('learning_reduced_events')} | "
            f"avg_weight={metrics.get('average_learning_weight')}"
        ),
        (
            "  Recommendations: "
            f"count={len(review.recommendations)} | reason={review.reason} | "
            f"threshold={MIN_PATTERN_OCCURRENCES}+ occurrences"
        ),
        (
            "  Safety: no LLM | no memory write | no config write | "
            "no auto-apply | no TTS/VTS/OBS/Discord/game input"
        ),
        "  Commands: /post-stream-review-status | /post-stream-review | /session-review-status",
    ]


def post_stream_review_lines() -> list[str]:
    review = build_post_stream_review()
    lines = [
        "🧾 Post-stream Review (STAGE-9C-A1)",
        "  Type: recommendation-only; nothing is applied automatically.",
        (
            "  Inputs: "
            f"reply_events={review.metrics.get('reply_events')} | "
            f"outbox_events={review.metrics.get('outbox_events')} | "
            f"missing_fields={review.metrics.get('missing_field_files')}"
        ),
        (
            "  Learning filter: "
            f"eligible={review.metrics.get('learning_eligible_events')} | "
            f"excluded={review.metrics.get('learning_excluded_events')} | "
            f"reduced={review.metrics.get('learning_reduced_events')}"
        ),
        f"  Recommendation count: {len(review.recommendations)} | reason={review.reason}",
    ]
    if review.observations:
        lines.append("  Observations:")
        for item in review.observations[:4]:
            lines.append(f"    - {_short(item, 170)}")
    if review.recommendations:
        lines.append("  Recommendations:")
        for index, rec in enumerate(review.recommendations, start=1):
            lines.append(
                f"    {index}. [{rec.type}] confidence={rec.confidence:.2f} | "
                f"occurrences={rec.occurrences} | unique_evidence={rec.evidence_count}"
            )
            if rec.evidence_sources:
                lines.append(f"       Sources: {', '.join(rec.evidence_sources)}")
            if rec.correlation_warning:
                lines.append(f"       Correlation: shares evidence with {', '.join(rec.correlated_with)}")
            lines.append(f"       Insight: {_short(rec.insight, 180)}")
            lines.append(f"       Metric: {_short(rec.metric, 160)}")
            lines.append(f"       Suggestion: {_short(rec.suggested_adjustment, 190)}")
            lines.append("       Evidence:")
            for snippet in rec.evidence_snippets[:3]:
                lines.append(f"         - \"{_short(snippet, 130)}\"")
    else:
        lines.append("  Recommendations: none")
    lines.append("  Safety: read_only=True | no LLM | no write | no auto-apply")
    return lines


def post_stream_review_snapshot() -> dict[str, Any]:
    return build_post_stream_review().to_dict()
