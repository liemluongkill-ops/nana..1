"""STAGE-9C-A0: Session review data adapter.

This adapter reads existing structured public-stage artifacts and turns them
into deterministic metrics for later post-stream review. It does not emit
recommendations, write memory, tune behavior, call LLMs, or perform live actions.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
import time
from typing import Any

from nana.config import BASE_DIR


PHASE = "STAGE-9C-A0"
DEFAULT_REPLIES_DIR = BASE_DIR / "data" / "external_bridge" / "replies"
DEFAULT_OUTBOX_SENT_DIR = BASE_DIR / "data" / "external_bridge" / "outbox" / "sent"
DEFAULT_LIMIT = 250


def _clean(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or fallback


def _short(value: Any, limit: int = 140) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _load_json(path: Path) -> tuple[dict[str, Any] | None, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        return None, f"json_error:{exc.__class__.__name__}"
    except OSError as exc:
        return None, f"io_error:{exc.__class__.__name__}"
    if not isinstance(payload, dict):
        return None, "not_object"
    return payload, ""


def _list_json_files(directory: Path, *, limit: int = DEFAULT_LIMIT) -> list[Path]:
    try:
        files = [path for path in directory.glob("*.json") if path.is_file()]
    except OSError:
        return []
    files.sort(key=_mtime, reverse=True)
    return files[: max(0, int(limit))]


@dataclass(frozen=True)
class SessionEvent:
    source_file: str
    request_id: str
    created_at: float
    ok: bool
    status: str
    reply_text_preview: str
    source: str
    event_type: str
    social_action: str
    social_reason: str
    social_event_type: str
    chat_velocity: float
    room_vibe: str
    director_mode: str
    response_shape: str
    viewer_name: str
    channel: str
    avatar_event: str
    public_quality_actions: tuple[str, ...] = field(default_factory=tuple)
    public_quality_violation_count: int = 0
    stage_identity_actions: tuple[str, ...] = field(default_factory=tuple)
    stage_identity_firewalled: bool = False
    stage_identity_violation_count: int = 0
    learning_eligible: bool = True
    learning_weight: float = 1.0
    test_noise_kind: str = "none"
    test_noise_score: float = 0.0
    test_noise_reasons: tuple[str, ...] = field(default_factory=tuple)
    reply_eval_grade: str = "unknown"
    reply_eval_score: float = 0.0
    reply_eval_issue_count: int = 0
    reply_eval_issue_kinds: tuple[str, ...] = field(default_factory=tuple)
    missing_fields: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class OutboxEvent:
    source_file: str
    event_id: str
    proposal_id: str
    created_at: float
    text_preview: str
    channel_id: str
    channel_name: str
    stream_state: str
    interaction_tone: str
    auto_send: bool
    approved_by_owner: bool
    source: str
    phase: str
    missing_fields: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SessionReviewDataset:
    reply_events: tuple[SessionEvent, ...]
    outbox_events: tuple[OutboxEvent, ...]
    corrupt_files: tuple[str, ...] = field(default_factory=tuple)
    missing_field_files: tuple[str, ...] = field(default_factory=tuple)
    scanned_reply_files: int = 0
    scanned_outbox_files: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply_events": [event.to_dict() for event in self.reply_events],
            "outbox_events": [event.to_dict() for event in self.outbox_events],
            "corrupt_files": list(self.corrupt_files),
            "missing_field_files": list(self.missing_field_files),
            "scanned_reply_files": self.scanned_reply_files,
            "scanned_outbox_files": self.scanned_outbox_files,
        }


@dataclass(frozen=True)
class SessionReviewMetrics:
    phase: str
    reply_files_scanned: int
    reply_events: int
    outbox_files_scanned: int
    outbox_events: int
    corrupt_files: int
    missing_field_files: int
    action_counts: dict[str, int]
    status_counts: dict[str, int]
    social_event_type_counts: dict[str, int]
    avatar_event_counts: dict[str, int]
    room_vibe_counts: dict[str, int]
    director_mode_counts: dict[str, int]
    response_shape_counts: dict[str, int]
    outbox_source_counts: dict[str, int]
    quality_violation_count: int
    stage_identity_violation_count: int
    stage_firewall_count: int
    learning_eligible_events: int
    learning_excluded_events: int
    learning_reduced_events: int
    average_learning_weight: float | None
    test_noise_counts: dict[str, int]
    reply_eval_grade_counts: dict[str, int]
    reply_eval_issue_counts: dict[str, int]
    reply_eval_warning_events: int
    reply_eval_fix_events: int
    full_reply_ratio: float | None
    ack_ratio: float | None
    skip_ratio: float | None
    emoji_sticker_ratio: float | None
    supported_metrics: dict[str, bool]
    sample_events: tuple[str, ...] = field(default_factory=tuple)
    read_only: bool = True
    can_act: bool = False
    recommendations_emitted: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def parse_reply_file(path: Path) -> tuple[SessionEvent | None, str]:
    payload, error = _load_json(path)
    if payload is None:
        return None, error

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    social = metadata.get("social_session") if isinstance(metadata.get("social_session"), dict) else {}
    avatar = metadata.get("avatar_event") if isinstance(metadata.get("avatar_event"), dict) else {}
    quality = metadata.get("public_quality") if isinstance(metadata.get("public_quality"), dict) else {}
    stage = metadata.get("stage_identity") if isinstance(metadata.get("stage_identity"), dict) else {}
    memory_filter = metadata.get("public_memory_filter") if isinstance(metadata.get("public_memory_filter"), dict) else {}
    reply_eval = metadata.get("public_reply_eval") if isinstance(metadata.get("public_reply_eval"), dict) else {}
    viewer_queue = metadata.get("viewer_queue") if isinstance(metadata.get("viewer_queue"), dict) else {}
    route = metadata.get("route") if isinstance(metadata.get("route"), dict) else {}

    missing: list[str] = []
    for key in ("request_id", "reply_text", "metadata"):
        if key not in payload:
            missing.append(key)
    if not social:
        missing.append("metadata.social_session")
    if not avatar:
        missing.append("metadata.avatar_event")

    quality_actions = quality.get("actions")
    stage_actions = stage.get("actions")
    quality_violations = quality.get("violations")
    stage_violations = stage.get("violations")
    filter_reasons = memory_filter.get("reasons")
    reply_eval_issues = reply_eval.get("issues")
    reply_eval_issue_kinds = reply_eval.get("issue_kinds")
    learning_weight = _as_float(memory_filter.get("learning_weight"), 1.0)
    if not memory_filter:
        learning_eligible = True
        learning_weight = 1.0
    else:
        learning_eligible = bool(memory_filter.get("learning_eligible", True))

    event = SessionEvent(
        source_file=str(path),
        request_id=_clean(payload.get("request_id"), path.stem),
        created_at=_as_float(avatar.get("created_at"), _mtime(path)),
        ok=bool(payload.get("ok", False)),
        status=_clean(payload.get("status")),
        reply_text_preview=_short(payload.get("reply_text")),
        source=_clean(metadata.get("source") or payload.get("source"), "unknown"),
        event_type=_clean(metadata.get("event_type") or payload.get("event_type"), "unknown"),
        social_action=_clean(social.get("action")),
        social_reason=_clean(social.get("reason")),
        social_event_type=_clean(social.get("event_type")),
        chat_velocity=_as_float(social.get("chat_velocity"), 0.0),
        room_vibe=_clean(social.get("room_vibe")),
        director_mode=_clean(social.get("director_mode")),
        response_shape=_clean(social.get("response_shape")),
        viewer_name=_clean(avatar.get("viewer_name") or viewer_queue.get("viewer_name"), "unknown"),
        channel=_clean(avatar.get("channel") or route.get("chat_channel_name"), "unknown"),
        avatar_event=_clean(avatar.get("event")),
        public_quality_actions=tuple(str(item) for item in quality_actions) if isinstance(quality_actions, list) else (),
        public_quality_violation_count=len(quality_violations) if isinstance(quality_violations, list) else 0,
        stage_identity_actions=tuple(str(item) for item in stage_actions) if isinstance(stage_actions, list) else (),
        stage_identity_firewalled=bool(stage.get("firewalled", False)),
        stage_identity_violation_count=len(stage_violations) if isinstance(stage_violations, list) else 0,
        learning_eligible=learning_eligible,
        learning_weight=max(0.0, min(1.0, learning_weight)),
        test_noise_kind=_clean(memory_filter.get("kind"), "none") if memory_filter else "none",
        test_noise_score=_as_float(memory_filter.get("noise_score"), 0.0) if memory_filter else 0.0,
        test_noise_reasons=tuple(str(item) for item in filter_reasons) if isinstance(filter_reasons, list) else (),
        reply_eval_grade=_clean(reply_eval.get("grade"), "unknown") if reply_eval else "unknown",
        reply_eval_score=_as_float(reply_eval.get("score"), 0.0) if reply_eval else 0.0,
        reply_eval_issue_count=len(reply_eval_issues) if isinstance(reply_eval_issues, list) else 0,
        reply_eval_issue_kinds=tuple(str(item) for item in reply_eval_issue_kinds) if isinstance(reply_eval_issue_kinds, list) else (),
        missing_fields=tuple(missing),
    )
    return event, ""


def parse_outbox_file(path: Path) -> tuple[OutboxEvent | None, str]:
    payload, error = _load_json(path)
    if payload is None:
        return None, error

    missing = [key for key in ("event_id", "proposal_id", "text") if key not in payload]
    event = OutboxEvent(
        source_file=str(path),
        event_id=_clean(payload.get("event_id"), path.stem),
        proposal_id=_clean(payload.get("proposal_id")),
        created_at=_as_float(payload.get("created_at"), _mtime(path)),
        text_preview=_short(payload.get("text")),
        channel_id=_clean(payload.get("channel_id"), ""),
        channel_name=_clean(payload.get("channel_name"), ""),
        stream_state=_clean(payload.get("stream_state")),
        interaction_tone=_clean(payload.get("interaction_tone")),
        auto_send=bool(payload.get("auto_send", False)),
        approved_by_owner=bool(payload.get("approved_by_owner", False)),
        source=_clean(payload.get("source")),
        phase=_clean(payload.get("phase")),
        missing_fields=tuple(missing),
    )
    return event, ""


def collect_review_data(
    *,
    replies_dir: Path | None = None,
    outbox_sent_dir: Path | None = None,
    limit: int = DEFAULT_LIMIT,
) -> SessionReviewDataset:
    reply_dir = Path(replies_dir or DEFAULT_REPLIES_DIR)
    outbox_dir = Path(outbox_sent_dir or DEFAULT_OUTBOX_SENT_DIR)
    reply_files = _list_json_files(reply_dir, limit=limit)
    outbox_files = _list_json_files(outbox_dir, limit=limit)

    reply_events: list[SessionEvent] = []
    outbox_events: list[OutboxEvent] = []
    corrupt_files: list[str] = []
    missing_field_files: list[str] = []

    for path in reply_files:
        event, error = parse_reply_file(path)
        if event is None:
            corrupt_files.append(f"{path}:{error}")
            continue
        reply_events.append(event)
        if event.missing_fields:
            missing_field_files.append(str(path))

    for path in outbox_files:
        event, error = parse_outbox_file(path)
        if event is None:
            corrupt_files.append(f"{path}:{error}")
            continue
        outbox_events.append(event)
        if event.missing_fields:
            missing_field_files.append(str(path))

    return SessionReviewDataset(
        reply_events=tuple(reply_events),
        outbox_events=tuple(outbox_events),
        corrupt_files=tuple(corrupt_files),
        missing_field_files=tuple(missing_field_files),
        scanned_reply_files=len(reply_files),
        scanned_outbox_files=len(outbox_files),
    )


def compute_metrics(dataset: SessionReviewDataset) -> SessionReviewMetrics:
    reply_events = list(dataset.reply_events)
    outbox_events = list(dataset.outbox_events)

    action_counts = Counter(event.social_action for event in reply_events)
    status_counts = Counter(event.status for event in reply_events)
    social_event_counts = Counter(event.social_event_type for event in reply_events)
    avatar_event_counts = Counter(event.avatar_event for event in reply_events)
    room_vibe_counts = Counter(event.room_vibe for event in reply_events)
    director_counts = Counter(event.director_mode for event in reply_events)
    shape_counts = Counter(event.response_shape for event in reply_events)
    outbox_source_counts = Counter(event.source for event in outbox_events)
    noise_counts = Counter(event.test_noise_kind for event in reply_events if event.test_noise_kind != "none")
    reply_eval_grade_counts = Counter(event.reply_eval_grade for event in reply_events if event.reply_eval_grade != "unknown")
    reply_eval_issue_counts = Counter(
        issue
        for event in reply_events
        for issue in event.reply_eval_issue_kinds
    )

    total = len(reply_events)
    eligible_count = sum(1 for event in reply_events if event.learning_eligible)
    excluded_count = sum(1 for event in reply_events if not event.learning_eligible)
    reduced_count = sum(1 for event in reply_events if event.learning_eligible and event.learning_weight < 1.0)
    average_weight = None
    if total:
        average_weight = round(sum(event.learning_weight for event in reply_events) / total, 3)
    emoji_or_sticker = sum(
        1
        for event in reply_events
        if event.social_event_type in {"emoji_only", "sticker"}
        or event.avatar_event in {"emoji_only", "sticker"}
    )

    def ratio(count: int) -> float | None:
        if not total:
            return None
        return round(count / total, 3)

    sample_events = tuple(
        _short(event.reply_text_preview or event.request_id, 90)
        for event in reply_events[:3]
        if (event.reply_text_preview or event.request_id)
    )

    supported = {
        "social_session": any("metadata.social_session" not in event.missing_fields for event in reply_events),
        "avatar_event": any("metadata.avatar_event" not in event.missing_fields for event in reply_events),
        "public_quality": any(event.public_quality_actions or event.public_quality_violation_count for event in reply_events),
        "stage_identity": any(event.stage_identity_actions or event.stage_identity_violation_count for event in reply_events),
        "outbox_sent": bool(outbox_events),
        "emoji_or_sticker_ratio": total > 0 and emoji_or_sticker > 0,
        "room_vibe_distribution": any(event.room_vibe != "unknown" for event in reply_events),
        "public_memory_filter": any(event.test_noise_kind != "none" for event in reply_events),
        "public_reply_eval": any(event.reply_eval_grade != "unknown" for event in reply_events),
    }

    return SessionReviewMetrics(
        phase=PHASE,
        reply_files_scanned=dataset.scanned_reply_files,
        reply_events=total,
        outbox_files_scanned=dataset.scanned_outbox_files,
        outbox_events=len(outbox_events),
        corrupt_files=len(dataset.corrupt_files),
        missing_field_files=len(dataset.missing_field_files),
        action_counts=dict(action_counts),
        status_counts=dict(status_counts),
        social_event_type_counts=dict(social_event_counts),
        avatar_event_counts=dict(avatar_event_counts),
        room_vibe_counts=dict(room_vibe_counts),
        director_mode_counts=dict(director_counts),
        response_shape_counts=dict(shape_counts),
        outbox_source_counts=dict(outbox_source_counts),
        quality_violation_count=sum(event.public_quality_violation_count for event in reply_events),
        stage_identity_violation_count=sum(event.stage_identity_violation_count for event in reply_events),
        stage_firewall_count=sum(1 for event in reply_events if event.stage_identity_firewalled),
        learning_eligible_events=eligible_count,
        learning_excluded_events=excluded_count,
        learning_reduced_events=reduced_count,
        average_learning_weight=average_weight,
        test_noise_counts=dict(noise_counts),
        reply_eval_grade_counts=dict(reply_eval_grade_counts),
        reply_eval_issue_counts=dict(reply_eval_issue_counts),
        reply_eval_warning_events=sum(1 for event in reply_events if event.reply_eval_issue_count and event.reply_eval_grade != "fix"),
        reply_eval_fix_events=sum(1 for event in reply_events if event.reply_eval_grade == "fix"),
        full_reply_ratio=ratio(action_counts.get("full_reply", 0)),
        ack_ratio=ratio(action_counts.get("ack_only", 0)),
        skip_ratio=ratio(action_counts.get("skip", 0)),
        emoji_sticker_ratio=ratio(emoji_or_sticker),
        supported_metrics=supported,
        sample_events=sample_events,
    )


def _format_counter(counter: dict[str, int], *, limit: int = 5) -> str:
    if not counter:
        return "none"
    items = sorted(counter.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return ", ".join(f"{key}={value}" for key, value in items)


def build_review_metrics(
    *,
    replies_dir: Path | None = None,
    outbox_sent_dir: Path | None = None,
    limit: int = DEFAULT_LIMIT,
) -> tuple[SessionReviewDataset, SessionReviewMetrics]:
    dataset = collect_review_data(replies_dir=replies_dir, outbox_sent_dir=outbox_sent_dir, limit=limit)
    return dataset, compute_metrics(dataset)


def session_review_status_lines() -> list[str]:
    dataset, metrics = build_review_metrics()
    return [
        "📊 Session Review Adapter (STAGE-9C-A0)",
        "  Mode: metrics-only | read_only=True | can_act=False | recommendations=0",
        (
            "  Sources: "
            f"replies={DEFAULT_REPLIES_DIR} | outbox_sent={DEFAULT_OUTBOX_SENT_DIR}"
        ),
        (
            "  Parsed: "
            f"reply_events={metrics.reply_events}/{metrics.reply_files_scanned} | "
            f"outbox_events={metrics.outbox_events}/{metrics.outbox_files_scanned} | "
            f"corrupt={metrics.corrupt_files} | missing_fields={metrics.missing_field_files}"
        ),
        (
            "  Supported: "
            + ", ".join(
                f"{key}={value}"
                for key, value in sorted(metrics.supported_metrics.items())
            )
        ),
        (
            "  Ratios: "
            f"full={metrics.full_reply_ratio} | ack={metrics.ack_ratio} | "
            f"skip={metrics.skip_ratio} | emoji/sticker={metrics.emoji_sticker_ratio}"
        ),
        (
            "  Counts: "
            f"actions[{_format_counter(metrics.action_counts)}] | "
            f"events[{_format_counter(metrics.social_event_type_counts)}] | "
            f"vibes[{_format_counter(metrics.room_vibe_counts)}]"
        ),
        (
            "  Quality: "
            f"public_quality_violations={metrics.quality_violation_count} | "
            f"stage_identity_violations={metrics.stage_identity_violation_count} | "
            f"firewalled={metrics.stage_firewall_count}"
        ),
        (
            "  Learning filter: "
            f"eligible={metrics.learning_eligible_events} | "
            f"excluded={metrics.learning_excluded_events} | "
            f"reduced={metrics.learning_reduced_events} | "
            f"avg_weight={metrics.average_learning_weight} | "
            f"noise[{_format_counter(metrics.test_noise_counts)}]"
        ),
        (
            "  Reply eval: "
            f"grades[{_format_counter(metrics.reply_eval_grade_counts)}] | "
            f"issues[{_format_counter(metrics.reply_eval_issue_counts)}] | "
            f"warn={metrics.reply_eval_warning_events} | fix={metrics.reply_eval_fix_events}"
        ),
        (
            "  Safety: no LLM | no memory write | no recommendations | "
            "no TTS/VTS/OBS/Discord/game input"
        ),
        "  Commands: /session-review-status | /session-review-preview | /post-stream-review-preview",
    ]


def session_review_preview_lines(limit: int = 5) -> list[str]:
    dataset, metrics = build_review_metrics(limit=DEFAULT_LIMIT)
    lines = [
        "📊 Session Review Preview (STAGE-9C-A0)",
        "  Type: metrics adapter only — no recommendation emitted in A0.",
        (
            "  Parsed: "
            f"reply_events={metrics.reply_events} | outbox_events={metrics.outbox_events} | "
            f"corrupt={metrics.corrupt_files} | missing_fields={metrics.missing_field_files}"
        ),
        (
            "  Metrics: "
            f"actions[{_format_counter(metrics.action_counts)}] | "
            f"avatar_events[{_format_counter(metrics.avatar_event_counts)}] | "
            f"outbox[{_format_counter(metrics.outbox_source_counts)}]"
        ),
        (
            "  Ratios: "
            f"full={metrics.full_reply_ratio} | ack={metrics.ack_ratio} | "
            f"skip={metrics.skip_ratio} | emoji/sticker={metrics.emoji_sticker_ratio}"
        ),
        (
            "  Learning filter: "
            f"eligible={metrics.learning_eligible_events} | excluded={metrics.learning_excluded_events} | "
            f"reduced={metrics.learning_reduced_events} | noise[{_format_counter(metrics.test_noise_counts)}]"
        ),
        (
            "  Reply eval: "
            f"grades[{_format_counter(metrics.reply_eval_grade_counts)}] | "
            f"issues[{_format_counter(metrics.reply_eval_issue_counts)}]"
        ),
    ]
    samples = list(metrics.sample_events)[:limit]
    if samples:
        lines.append("  Sample reply evidence:")
        for sample in samples:
            lines.append(f"    - {sample}")
    else:
        lines.append("  Sample reply evidence: none")
    if dataset.corrupt_files:
        lines.append("  Corrupt files:")
        for item in dataset.corrupt_files[:3]:
            lines.append(f"    - {_short(item, 120)}")
    lines.append("  Next: STAGE-9C-A1 may consume these metrics to create recommendation-only review.")
    lines.append("  Safety: read_only=True | no LLM | no write | no auto-apply")
    return lines


def session_review_snapshot() -> dict[str, Any]:
    _, metrics = build_review_metrics()
    return metrics.to_dict()
