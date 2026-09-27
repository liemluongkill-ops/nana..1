"""Nana-side external bridge worker.

STAGE-7C consumes request JSON files written by external transports such as the
Discord bridge. The bridge stays transport-only; Nana core owns persona
boundary, viewer queue ingestion, public reply text, and safety flags.

This worker is text-only for now:
- no local speaker playback
- no VTS
- no OBS/subtitle write
- no game input
- reply JSON always uses speak=false and local_playback=false
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace, asdict
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import time
import uuid
from typing import Any, Callable

from nana.runtime.persona_boundary import public_safe_fallback_for_message, sanitize_public_reply
from nana.runtime.livestream_identity import finalize_livestream_identity, is_livestream_source
from nana.runtime.public_fallback_recovery import recover_public_fallback
from nana.runtime.public_fluency_polish import polish_public_vietnamese
from nana.runtime.public_memory_filter import get_public_memory_filter
from nana.runtime.public_quality import get_public_quality_guard
from nana.runtime.public_reply_evaluator import evaluate_public_reply
from nana.runtime.public_stage_identity import get_public_stage_identity_guard
from nana.runtime.social_session import SocialSessionCache, get_social_session
from nana.runtime.stream_event_timeline import record_stream_event
from nana.runtime.viewer_chat import ViewerChatQueue, get_viewer_chat_queue
from nana.runtime.avatar_event_bridge import get_avatar_event_bridge
from nana.runtime.avatar_live_hook import get_avatar_live_hook
from nana.runtime.public_context_boundary import PublicEventScope, _scope_from_metadata
from nana.runtime.public_delivery_state import PublicDeliveryRecord, transition_delivery


DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DEFAULT_REQUEST_DIR = DATA_DIR / "external_bridge" / "requests"
DEFAULT_REPLY_DIR = DATA_DIR / "external_bridge" / "replies"
DEFAULT_POLL_SECONDS = 0.35
DEFAULT_MAX_FILES_PER_POLL = 3
DEFAULT_EVENT_DEDUPE_TTL_SECONDS = 600.0
PHASE = "STAGE-7H"

Responder = Callable[["ExternalBridgeRequest"], str]

_PUBLIC_MODEL_TOPIC_RE = re.compile(
    r"\b(?:gpt|llm|model|mô\s*hình|gemini|claude|grok|sonnet|opus|haiku|flash|mini|sol|5\.[0-9])\b",
    re.IGNORECASE,
)

_PUBLIC_QUIET_ROOM_MARKERS = (
    "phòng im",
    "phòng nay im",
    "im quá",
    "vắng quá",
    "yên tĩnh",
    "trầm quá",
    "không ai nói",
    "không có ai",
)

_SIMILAR_QUESTION_COUNT_RE = re.compile(r"similar_question_count_this_session\s*=\s*(\d+)", re.IGNORECASE)
_QUIET_ROOM_REPEAT_AWARE_THRESHOLD = 3
_QUIET_ROOM_FATIGUE_THRESHOLD = 6


def _env_enabled(name: str, default: bool = True) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return str(raw).strip().lower() not in {"0", "false", "no", "off", ""}


def _path_from_env(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    return Path(raw).expanduser() if raw else default


def _short(value: Any, limit: int = 120) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _short_hash(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _is_identity_challenge_text(value: Any) -> bool:
    lower = " ".join(str(value or "").lower().split())
    if "nana" not in lower:
        return False
    flattens = any(
        token in lower
        for token in (
            "bot discord",
            "chatbot",
            "command bot",
            "trợ lý",
            "assistant",
            "công cụ",
            "tool",
            "hộp trả lời",
            "máy trả lời",
        )
    )
    asks = "?" in lower or any(
        token in lower
        for token in (
            "đúng không",
            "phải không",
            "chỉ là",
            "có phải",
            "là bot",
            "thôi",
        )
    )
    return flattens and asks


def _is_service_role_request_text(value: Any) -> bool:
    lower = " ".join(str(value or "").lower().split())
    if "nana" not in lower:
        return False
    has_service_role = any(
        token in lower
        for token in ("trợ lý phục vụ", "làm trợ lý", "assistant", "service", "phục vụ", "quầy hỗ trợ")
    )
    has_direct_request = any(
        token in lower
        for token in ("cho tôi", "cho t", "đi", "làm", "hãy", "giúp", "phục vụ")
    )
    return has_service_role and has_direct_request


def _is_public_model_topic_request(value: Any) -> bool:
    return bool(str(value or "").strip() and _PUBLIC_MODEL_TOPIC_RE.search(str(value or "")))


def _is_public_short_quiet_room_request(value: Any) -> bool:
    lowered = " ".join(str(value or "").lower().split())
    if not lowered:
        return False
    if not any(marker in lowered for marker in _PUBLIC_QUIET_ROOM_MARKERS):
        return False
    return len(lowered.split()) <= 8


def _safe_public_identity_boundary_reply() -> str:
    try:
        from nana.runtime.public_voice_style import public_identity_boundary_reply

        return public_identity_boundary_reply(seed=f"bridge:{time.time_ns()}")
    except Exception:
        return "Nana là Nana chứ. Vào phòng Nana mà gọi quầy hỗ trợ thì hơi oan cho sân khấu này đó nha."


def _safe_public_service_boundary_reply() -> str:
    try:
        from nana.runtime.public_voice_style import public_service_boundary_reply

        return public_service_boundary_reply(seed=f"bridge-service:{time.time_ns()}")
    except Exception:
        return (
            "Nana không nhận vai trợ lý phục vụ đâu. "
            "Nana có thể trò chuyện, chơi game và làm sân khấu vui hơn, nhưng không phải quầy hỗ trợ."
        )


def _safe_public_light_ack_reply(*, event_type: str = "seen", reason: str = "", seed: str = "") -> str:
    kind = "fast" if reason == "chat_velocity_medium" else event_type
    try:
        from nana.runtime.public_voice_style import public_light_ack_reply

        return public_light_ack_reply(kind=kind, seed=seed or f"bridge-ack:{time.time_ns()}")
    except Exception:
        return "Nana bắt được tín hiệu rồi nha."


def _safe_public_full_reply_polish(
    text: str,
    *,
    user_text: str = "",
    room_vibe: str = "quiet_room",
    seed: str = "",
) -> str:
    try:
        from nana.runtime.public_voice_style import public_full_reply_polish

        return public_full_reply_polish(
            text,
            user_text=user_text,
            room_vibe=room_vibe,
            seed=seed or f"bridge-full:{time.time_ns()}",
        )
    except Exception:
        return str(text or "")


def _style_hint_similar_question_count(style_hint: Any) -> int:
    match = _SIMILAR_QUESTION_COUNT_RE.search(str(style_hint or ""))
    if not match:
        return 0
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return 0


def _safe_public_quiet_room_repeat_reply(*, repeat_count: int = 3, seed: str = "") -> str:
    try:
        from nana.runtime.public_voice_style import public_quiet_room_repeat_reply

        return public_quiet_room_repeat_reply(
            repeat_count=repeat_count,
            seed=seed or f"bridge-quiet-repeat:{repeat_count}:{time.time_ns()}",
        )
    except Exception:
        return (
            "Câu này quay lại hơi nhiều rồi nha. "
            "Nana bắt đầu nghi đây là bài kiểm tra độ kiên nhẫn của phòng, không chỉ là phòng im nữa."
        )


def _safe_public_quiet_room_fatigue_reply(*, repeat_count: int = 6, seed: str = "") -> str:
    try:
        from nana.runtime.public_voice_style import public_quiet_room_fatigue_reply

        return public_quiet_room_fatigue_reply(
            repeat_count=repeat_count,
            seed=seed or f"bridge-quiet-fatigue:{repeat_count}:{time.time_ns()}",
        )
    except Exception:
        return "Bài test “phòng im” Nana nhận rồi nha. Đổi câu đi, không là phòng này thành phòng thí nghiệm mất."


def _safe_public_quiet_room_fresh_reply(*, seed: str = "") -> str:
    try:
        from nana.runtime.public_voice_style import public_quiet_room_fresh_reply

        return public_quiet_room_fresh_reply(seed=seed or f"bridge-quiet-fresh:{time.time_ns()}")
    except Exception:
        return (
            "Phòng yên tới mức Nana nghe như cái chat vừa kéo chăn lên ngủ. "
            "Nana gõ nhẹ lên bàn một cái: ai còn thức thì thả một chi tiết lạ hôm nay."
        )


def _safe_public_quiet_room_rhythm_reply(
    *,
    text: str = "",
    repeat_count: int = 1,
    feedback_mode: str = "none",
    seed: str = "",
) -> str:
    try:
        from nana.runtime.public_quiet_room_rhythm import public_quiet_room_rhythm_reply

        reply = public_quiet_room_rhythm_reply(
            text=text,
            repeat_count=repeat_count,
            feedback_mode=feedback_mode,
            seed=seed or f"bridge-quiet-rhythm:{repeat_count}:{time.time_ns()}",
        )
        if reply:
            return reply
    except Exception:
        pass
    return (
        "Phòng đang yên như có ai đặt tay lên nút pause. "
        "Nana gõ nhẹ lên bàn: hôm nay có khoảnh khắc nào đáng kể không?"
    )


def _latest_public_reply_feedback_mode() -> str:
    try:
        from nana.runtime.public_reply_feedback import get_public_reply_feedback

        last = get_public_reply_feedback().snapshot().get("last_directive")
    except Exception:
        return "none"
    if not isinstance(last, dict):
        return "none"
    return str(last.get("mode") or "none")


@dataclass(frozen=True)
class ExternalBridgeRequest:
    request_id: str
    source: str
    event_type: str
    text: str
    guild_id: int | None
    channel_id: int | str | None
    voice_channel_id: int | None
    author_id: int | str | None
    author_name: str
    audio_target: str
    local_playback: bool
    created_at: float
    metadata: dict[str, Any]
    path: Path | None = None
    scope: PublicEventScope | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any], *, path: Path | None = None) -> "ExternalBridgeRequest":
        request_id = str(payload.get("request_id") or (path.stem if path else f"external-{int(time.time() * 1000)}"))
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return cls(
            request_id=request_id,
            source=str(payload.get("source") or "external"),
            event_type=str(payload.get("event_type") or "message"),
            text=str(payload.get("text") or ""),
            guild_id=_optional_int(payload.get("guild_id")),
            channel_id=_optional_id(payload.get("channel_id")),
            voice_channel_id=_optional_int(payload.get("voice_channel_id")),
            author_id=_optional_id(payload.get("author_id")),
            author_name=str(payload.get("author_name") or "viewer"),
            audio_target=str(payload.get("audio_target") or "discord_voice"),
            local_playback=bool(payload.get("local_playback", False)),
            created_at=float(payload.get("created_at") or time.time()),
            metadata=metadata,
            path=path,
        )

    @property
    def channel_label(self) -> str:
        route = self.metadata.get("route") if isinstance(self.metadata.get("route"), dict) else {}
        channel_name = self.metadata.get("channel_name") or route.get("chat_channel_name")
        return str(channel_name or self.channel_id or "external")

    @property
    def event_key(self) -> str:
        message_id = self.metadata.get("message_id") if isinstance(self.metadata, dict) else None
        if message_id is None or message_id == "":
            return ""
        return f"{self.source or 'external'}:{self.guild_id or '-'}:{self.channel_id or '-'}:{message_id}"


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_id(value: Any) -> str | int | None:
    """Adapter boundary: preserve platform-issued opaque IDs, including zero."""
    if value is None or value == "":
        return None
    return value if type(value) is int else str(value).strip() or None


class ExternalBridgeRuntime:
    def __init__(
        self,
        *,
        request_dir: Path | None = None,
        reply_dir: Path | None = None,
        queue: ViewerChatQueue | None = None,
        social_session: SocialSessionCache | None = None,
        enabled: bool | None = None,
        poll_seconds: float | None = None,
        max_files_per_poll: int = DEFAULT_MAX_FILES_PER_POLL,
    ) -> None:
        self.request_dir = request_dir or _path_from_env("NANA_REQUEST_DIR", DEFAULT_REQUEST_DIR)
        self.reply_dir = reply_dir or _path_from_env("NANA_REPLY_DIR", DEFAULT_REPLY_DIR)
        self.processed_dir = self.request_dir / "processed"
        self.failed_dir = self.request_dir / "failed"
        self.queue = queue or get_viewer_chat_queue()
        self.social_session = social_session or get_social_session()
        self.enabled = _env_enabled("NANA_EXTERNAL_BRIDGE_ENABLED", True) if enabled is None else bool(enabled)
        self.poll_seconds = float(os.environ.get("NANA_EXTERNAL_BRIDGE_POLL_SECONDS", poll_seconds or DEFAULT_POLL_SECONDS))
        self.max_files_per_poll = int(max_files_per_poll)
        self._lock = threading.Lock()
        self._stop_requested = False
        self._running = False
        self._stats = {
            "processed": 0,
            "failed": 0,
            "reply_written": 0,
            "queued": 0,
            "duplicate": 0,
            "rate_limited": 0,
            "empty": 0,
            "social_full_reply": 0,
            "social_ack": 0,
            "social_skip": 0,
        }
        self._last_request_id = ""
        self._last_error = ""
        self._last_reply_preview = ""
        self._last_processed_at = 0.0
        self._seen_event_keys: dict[str, float] = {}
        self.event_dedupe_ttl_seconds = DEFAULT_EVENT_DEDUPE_TTL_SECONDS
        self._memory_session_id = "bridge-" + uuid.uuid4().hex
        self._output_records: dict[str, PublicDeliveryRecord] = {}

    def _canonical_scope(self, request: ExternalBridgeRequest) -> PublicEventScope:
        if request.scope is not None:
            return request.scope
        meta = dict(request.metadata)
        meta.update(platform=request.source, author_id=request.author_id,
                    room_id=request.channel_id if request.channel_id is not None else "request:" + request.request_id,
                    display_name=request.author_name, request_id=request.request_id)
        if not meta.get("stream_session_id"):
            meta["stream_session_id"] = self._memory_session_id
        return _scope_from_metadata(request.author_name, request.source, meta)

    def record_delivery_receipt(self, receipt: dict[str, Any]) -> bool:
        """Output-adapter hook only; viewer text never reaches this method.

        Legacy text-only adapters supply no playback receipts and remain
        published. This method does not manufacture playback from text/ACKs.
        """
        output_id = receipt.get("output_id")
        with self._lock:
            old = self._output_records.get(output_id)
            if old is None:
                return False
            updated = transition_delivery(old, receipt.get("state"), receipt.get("revision"),
                                          receipt.get("attempt_id"), receipt)
            if updated is old:
                return False
            self._output_records[output_id] = updated
            # Preserve callback order through the session projection too.
            self.social_session.record_reply_context(scope=updated.scope, delivery_record=updated)
        return True

    def _record_output_state(self, output_id: str, state: str) -> None:
        with self._lock:
            old = self._output_records.get(output_id)
        if old is None:
            return
        receipt = {"event_id": old.event_id, "output_id": old.output_id,
                   "attempt_id": old.attempt_id, "platform": old.scope.platform,
                   "room_id": old.scope.room_id, "stream_session_id": old.scope.stream_session_id,
                   "state": state, "revision": old.revision + 1,
                   "timestamp": max(time.time(), old.updated_at)}
        self.record_delivery_receipt(receipt)

    def ensure_dirs(self) -> None:
        self.request_dir.mkdir(parents=True, exist_ok=True)
        self.reply_dir.mkdir(parents=True, exist_ok=True)
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        self.failed_dir.mkdir(parents=True, exist_ok=True)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            pending = 0
            try:
                pending = len([p for p in self.request_dir.glob("*.json") if p.is_file()])
            except OSError:
                pending = 0
            return {
                "phase": PHASE,
                "enabled": self.enabled,
                "running": self._running,
                "request_dir": str(self.request_dir),
                "reply_dir": str(self.reply_dir),
                "processed_dir": str(self.processed_dir),
                "failed_dir": str(self.failed_dir),
                "pending": pending,
                "poll_seconds": self.poll_seconds,
                "max_files_per_poll": self.max_files_per_poll,
                "stats": dict(self._stats),
                "last_request_id": self._last_request_id,
                "last_error": self._last_error,
                "last_reply_preview": self._last_reply_preview,
                "last_processed_age": None if not self._last_processed_at else max(0.0, time.time() - self._last_processed_at),
                "event_dedupe_keys": len(self._seen_event_keys),
                "social_session": self.social_session.snapshot(),
                "safety": {
                    "can_act": False,
                    "voice_call": False,
                    "vts_call": False,
                    "obs_call": False,
                    "game_input": False,
                    "speak": False,
                    "local_playback": False,
                },
            }

    def poll_once(self, *, responder: Responder | None = None, max_files: int | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"processed": 0, "failed": 0, "reason": "disabled"}
        self.ensure_dirs()
        files = sorted(
            [p for p in self.request_dir.glob("*.json") if p.is_file()],
            key=lambda p: (p.stat().st_mtime, p.name),
        )
        limit = self.max_files_per_poll if max_files is None else int(max_files)
        processed = 0
        failed = 0
        for path in files[:limit]:
            try:
                request = self._read_request(path)
                reply = self.process_request(request, responder=responder)
                self._write_reply(request.request_id, reply)
                self._move_request(path, self.processed_dir)
                processed += 1
            except Exception as exc:
                failed += 1
                self._record_failure(path, exc)
        return {"processed": processed, "failed": failed, "reason": "ok"}

    def process_request(self, request: ExternalBridgeRequest, *, responder: Responder | None = None) -> dict[str, Any]:
        scope = self._canonical_scope(request)
        request = replace(request, scope=scope)
        with self._lock:
            prior_output = self._output_records.get(request.request_id)
        if prior_output is not None:
            if prior_output.scope != scope:
                # Display-name changes do not matter; event/output ownership does.
                if (prior_output.scope.platform, prior_output.scope.room_id,
                    prior_output.scope.stream_session_id, prior_output.event_id) != (
                        scope.platform, scope.room_id, scope.stream_session_id, scope.event_id):
                    raise ValueError("request_id already belongs to another public event")
            return self._duplicate_reply(request, local_playback=False, status="duplicate_external_event")
        if request.local_playback:
            # Discord requests must never request local playback; force the safe boundary.
            local_playback = False
        else:
            local_playback = False
        record_stream_event(
            "bridge_request_start",
            request_id=request.request_id,
            source=request.source,
            event_type=request.event_type,
            channel=request.channel_label,
            viewer=request.author_name,
            has_text=bool(request.text),
            text_chars=len(request.text or ""),
            text_hash=_short_hash(request.text),
            local_playback_requested=bool(request.local_playback),
        )

        duplicate_event = False
        event_key = (scope.platform, scope.room_id, scope.stream_session_id, scope.event_id)
        if event_key:
            now_mono = time.monotonic()
            with self._lock:
                self._expire_event_keys_locked(now_mono)
                duplicate_event = event_key in self._seen_event_keys
                if not duplicate_event:
                    # Mark before the LLM call. If the transport writes two files
                    # with the same Discord message_id, the second one must not wait
                    # for the first model call to finish before being suppressed.
                    self._seen_event_keys[event_key] = now_mono
                else:
                    self._stats["duplicate"] += 1
            if duplicate_event:
                record_stream_event(
                    "bridge_duplicate_event",
                    request_id=request.request_id,
                    source=request.source,
                    event_type=request.event_type,
                    channel=request.channel_label,
                    viewer=request.author_name,
                    status="duplicate_external_event",
                )
                return self._duplicate_reply(request, local_playback=local_playback, status="duplicate_external_event")

        submit = self.queue.submit(
            platform=request.source or "discord",
            channel=request.channel_label,
            viewer_name=request.author_name,
            text=request.text,
            source_id=request.request_id,
            allow_duplicate_text=bool(event_key),
        )
        reason = str(submit.get("reason") or "unknown")
        with self._lock:
            if submit.get("accepted"):
                self._stats["queued"] += 1
            elif reason == "duplicate_recent":
                self._stats["duplicate"] += 1
            elif reason == "rate_limited":
                self._stats["rate_limited"] += 1
            elif reason == "empty_text":
                self._stats["empty"] += 1
        record_stream_event(
            "viewer_queue",
            request_id=request.request_id,
            accepted=bool(submit.get("accepted")),
            reason=reason,
            queued=submit.get("queued"),
            message_id=getattr(submit.get("message"), "id", None),
            priority=submit.get("priority", "normal"),
        )

        message = submit.get("message")
        social_decision = None
        avatar_event_record = None
        public_quality_result: dict[str, Any] | None = None
        stage_identity_result: dict[str, Any] | None = None
        public_fallback_recovery_result: dict[str, Any] | None = None
        public_fluency_result: dict[str, Any] | None = None
        public_memory_filter_result: dict[str, Any] | None = None
        public_reply_eval_result: dict[str, Any] | None = None
        if submit.get("accepted"):
            social_decision = self.social_session.observe(
                platform=request.source or "discord",
                channel=request.channel_label,
                viewer_name=request.author_name,
                text=request.text,
                event_type=request.event_type,
                priority=getattr(message, "priority", submit.get("priority", "normal")),
                metadata=request.metadata,
                scope=scope,
            )
            self.social_session.record_public_turn(scope=scope, text=request.text,
                                                  revision=0, topic=request.text[:60])
            record_stream_event(
                "social_decision",
                request_id=request.request_id,
                action=social_decision.action,
                reason=social_decision.reason,
                event_type=social_decision.event_type,
                should_call_llm=social_decision.should_call_llm,
                velocity=round(float(social_decision.chat_velocity), 3),
                priority=social_decision.priority_score,
            )
            try:
                # STAGE-9M is built inside social_session style hints. Capture
                # that result here so session review can discount repeated tests.
                public_memory_filter_result = (
                    get_public_memory_filter().snapshot().get("last_directive")
                )
            except Exception:
                public_memory_filter_result = None
            avatar_event_record = get_avatar_event_bridge().observe_public_event(
                text=request.text,
                event_type=social_decision.event_type if social_decision else request.event_type,
                metadata=request.metadata,
                source=request.source or "discord",
                viewer_name=request.author_name,
                channel=request.channel_label,
                request_id=request.request_id,
                reason="external_bridge_request",
            )
            record_stream_event(
                "avatar_event_planned",
                request_id=request.request_id,
                event_type=social_decision.event_type if social_decision else request.event_type,
                accepted=True,
                source=request.source or "discord",
                viewer=request.author_name,
            )
            try:
                get_avatar_live_hook().evaluate_latest(dry_run=False)
            except Exception:
                # Avatar live hook metadata must never block public reply handling.
                pass
            try:
                if social_decision.action == "full_reply":
                    with self._lock:
                        self._stats["social_full_reply"] += 1
                    # STAGE-7G: Public command firewall. If viewer typed a backstage
                    # command, bypass the LLM and return a stage-safe line.
                    stage_guard = get_public_stage_identity_guard()
                    firewall_line = stage_guard.public_command_firewall(
                        request.text,
                        social_decision_action=social_decision.action,
                    )
                    if firewall_line is not None:
                        reply_text = sanitize_public_reply(
                            firewall_line,
                            fallback=public_safe_fallback_for_message(request.text, viewer_name=request.author_name),
                            user_text=request.text,
                            viewer_name=request.author_name,
                        )
                        stage_identity_result = {
                            "actions": ["firewall"],
                            "firewalled": True,
                            "violations": [
                                {"kind": "backstage_command", "detail": f"input matched backstage command: {request.text!r}", "action": "firewall"}
                            ],
                            "phase": "STAGE-7H",
                        }
                        status = "stage_firewalled"
                    else:
                        used_default_responder = responder is None
                        quiet_repeat_count = 0
                        if used_default_responder and _is_public_short_quiet_room_request(request.text):
                            quiet_repeat_count = _style_hint_similar_question_count(social_decision.style_hint)
                        if used_default_responder and quiet_repeat_count >= _QUIET_ROOM_FATIGUE_THRESHOLD:
                            raw_reply = _safe_public_quiet_room_fatigue_reply(
                                repeat_count=quiet_repeat_count,
                                seed=f"{request.request_id}:{request.author_name}:{request.text}",
                            )
                        elif used_default_responder and quiet_repeat_count >= _QUIET_ROOM_REPEAT_AWARE_THRESHOLD:
                            raw_reply = _safe_public_quiet_room_repeat_reply(
                                repeat_count=quiet_repeat_count,
                                seed=f"{request.request_id}:{request.author_name}:{request.text}",
                            )
                        elif (
                            used_default_responder
                            and _is_public_short_quiet_room_request(request.text)
                            and self.social_session.feedback_mode(scope, request.text) == "avoid_menu_loop"
                        ):
                            raw_reply = _safe_public_quiet_room_fresh_reply(
                                seed=f"{request.request_id}:{request.author_name}:{request.text}:feedback"
                            )
                        elif used_default_responder and _is_public_short_quiet_room_request(request.text):
                            raw_reply = _safe_public_quiet_room_rhythm_reply(
                                text=request.text,
                                repeat_count=max(1, quiet_repeat_count),
                                feedback_mode=self.social_session.feedback_mode(scope, request.text),
                                seed=f"{request.request_id}:{request.author_name}:{request.text}:quiet-rhythm",
                            )
                        elif responder is not None:
                            raw_reply = responder(request)
                        else:
                            raw_reply = _default_public_text_responder(
                                request,
                                public_style_hint=social_decision.style_hint,
                            )
                        raw_reply = _repair_public_core_self_reply(raw_reply, request.text)
                        reply_text = sanitize_public_reply(
                            raw_reply,
                            fallback=public_safe_fallback_for_message(request.text, viewer_name=request.author_name),
                            user_text=request.text,
                            viewer_name=request.author_name,
                        )
                        if not (
                            used_default_responder
                            and (
                                _is_public_model_topic_request(request.text)
                                or _is_public_short_quiet_room_request(request.text)
                            )
                        ):
                            reply_text = _safe_public_full_reply_polish(
                                reply_text,
                                user_text=request.text,
                                room_vibe=getattr(social_decision, "room_vibe", "quiet_room") or "quiet_room",
                                seed=f"{request.request_id}:{request.author_name}:{request.text}",
                            )
                        status = "ok"
                elif social_decision.action == "ack_only":
                    with self._lock:
                        self._stats["social_ack"] += 1
                    ack_seed = (
                        f"{request.request_id}:"
                        f"{request.author_name}:"
                        f"{social_decision.event_type}:"
                        f"{social_decision.reason}:"
                        f"{request.text}"
                    )
                    ack_text = _safe_public_light_ack_reply(
                        event_type=social_decision.event_type,
                        reason=social_decision.reason,
                        seed=ack_seed,
                    )
                    reply_text = sanitize_public_reply(
                        ack_text,
                        fallback="Nana bắt được tín hiệu rồi nha.",
                        user_text=request.text,
                        viewer_name=request.author_name,
                    )
                    status = "social_ack"
                else:
                    with self._lock:
                        self._stats["social_skip"] += 1
                    reply_text = ""
                    status = "social_skip"
            except Exception as exc:
                reply_text = f"Nana đang bị lỗi khi trả lời public chat: {type(exc).__name__}."
                status = "ok"

            # STAGE-7F: Public reply quality guard runs AFTER sanitize_public_reply
            # and BEFORE record_reply_context / reply JSON assembly.
            if reply_text and social_decision is not None:
                guard = get_public_quality_guard()
                room_vibe = getattr(social_decision, "room_vibe", "quiet_room") or "quiet_room"
                last_addressed = self.social_session._last_addressed_viewer
                guard_result = guard.guard_reply(
                    reply_text,
                    viewer_name=request.author_name,
                    room_vibe=str(room_vibe),
                    last_addressed_viewer=last_addressed,
                )
                reply_text = guard_result.text
                public_quality_result = {
                    "actions": list(guard_result.actions),
                    "max_chars": guard_result.max_chars,
                    "violations": [
                        {"kind": v.kind, "detail": v.detail, "action": v.action}
                        for v in guard_result.violations
                    ],
                    "guard_phase": "STAGE-7F",
                }
                if "fallback" in guard_result.actions:
                    recovery = recover_public_fallback(
                        source_text=guard_result.original,
                        viewer_text=request.text,
                        violation_kinds=tuple(v.kind for v in guard_result.violations),
                        seed=f"{request.request_id}:{request.author_name}:{request.text}:quality",
                        running_joke={},  # A global last-joke cache has no room/session provenance.
                    )
                    if recovery.text.strip():
                        reply_text = recovery.text
                        public_fallback_recovery_result = recovery.to_dict()

            # STAGE-7G: Stage identity rewrite. Runs AFTER public quality guard.
            # Strips assistant/service wording, blocks operator/tech leak, blocks Ba/con.
            # If the LLM reply slipped into "support assistant" voice, rewrite/fallback.
            if reply_text and social_decision is not None and not stage_identity_result:
                stage_guard = get_public_stage_identity_guard()
                stage_result = stage_guard.rewrite_public_stage_reply(
                    reply_text,
                    viewer_name=request.author_name,
                )
                reply_text = stage_result.text
                stage_identity_result = {
                    "actions": list(stage_result.actions),
                    "firewalled": stage_result.was_firewalled,
                    "violations": [
                        {"kind": v.kind, "detail": v.detail, "action": v.action}
                        for v in stage_result.violations
                    ],
                    "phase": "STAGE-7H",
                }

            # STAGE-9P: deterministic Vietnamese fluency polish. Runs after all
            # public safety guards and before context logging / reply JSON.
            if reply_text and social_decision is not None:
                fluency_result = polish_public_vietnamese(
                    reply_text,
                    viewer_text=request.text,
                    source="external_bridge",
                )
                reply_text = fluency_result.text
                public_fluency_result = fluency_result.to_dict()

            # STAGE-9Q: final public reply evaluator. Diagnostic metadata only;
            # this never rewrites, blocks, calls an LLM, or changes delivery.
            if reply_text and social_decision is not None:
                reply_eval = evaluate_public_reply(
                    request.text,
                    reply_text,
                    source="external_bridge",
                )
                public_reply_eval_result = reply_eval.to_dict()
                self.social_session.record_evaluation(scope, public_reply_eval_result)

            if social_decision is not None:
                reply_text = finalize_livestream_identity(reply_text, source=request.source, viewer_name=request.author_name)
                if reply_text:
                    generated = PublicDeliveryRecord(scope.event_id, request.request_id,
                        "generated", "attempt-1", 1, reply_text, scope, time.time())
                    with self._lock:
                        if len(self._output_records) >= 256:
                            self._output_records.pop(next(iter(self._output_records)))
                        self._output_records[request.request_id] = generated
                    self.social_session.record_reply_context(scope=scope, delivery_record=generated)
            ok = True
            error = None
        elif reason == "duplicate_recent":
            # Keep the bridge wait contract satisfied but do not mirror duplicate
            # text back to Discord. Multiple bridge instances can receive the
            # same Discord message_id; a non-empty duplicate ack would spam chat.
            reply_text = ""
            ok = True
            status = "duplicate_recent"
            error = None
        elif reason == "rate_limited":
            reply_text = "Nana đọc được rồi, chờ Nana xíu nha."
            ok = True
            status = "rate_limited"
            error = None
        else:
            reply_text = "Nana chưa thấy nội dung để trả lời."
            ok = True
            status = reason
            error = None

        reply_text = finalize_livestream_identity(reply_text, source=request.source, viewer_name=request.author_name)
        record_stream_event(
            "reply_text_ready",
            request_id=request.request_id,
            status=status,
            ok=ok,
            has_reply=bool(reply_text),
            chars=len(reply_text or ""),
            social_action=getattr(social_decision, "action", None),
            speak=False,
        )

        metadata = {
            "phase": PHASE,
            "memory_scope": asdict(scope),
            "delivery": {"event_id": scope.event_id, "output_id": request.request_id,
                         "attempt_id": "attempt-1", "state": "generated", "revision": 1},
            "source": request.source,
            "event_type": request.event_type,
            "viewer_queue": {
                "accepted": bool(submit.get("accepted")),
                "reason": reason,
                "queued": submit.get("queued"),
                "message_id": getattr(message, "id", None),
                "priority": getattr(message, "priority", submit.get("priority", "normal")),
                "persona_lane": getattr(message, "persona_lane", "public_vtuber_persona"),
                "interaction_scope": getattr(message, "interaction_scope", "public_viewer"),
                "memory_policy": getattr(message, "memory_policy", "public_safe_only_no_private_owner_memory"),
            },
            "social_session": social_decision.to_dict() if social_decision else None,
            "avatar_event": avatar_event_record.to_dict() if submit.get("accepted") else None,
            "public_quality": public_quality_result,
            "stage_identity": stage_identity_result,
            "public_fallback_recovery": public_fallback_recovery_result,
            "public_fluency": public_fluency_result,
            "public_reply_eval": public_reply_eval_result,
            "public_memory_filter": public_memory_filter_result,
            "safety": {
                "can_act": False,
                "voice_call": False,
                "vts_call": False,
                "obs_call": False,
                "game_input": False,
                "local_playback": False,
            },
            "route": request.metadata.get("route") if isinstance(request.metadata.get("route"), dict) else {},
        }
        reply = {
            "request_id": request.request_id,
            "ok": ok,
            "reply_text": reply_text,
            "speak": False,
            "audio_paths": [],
            "audio_target": request.audio_target or "discord_voice",
            "local_playback": local_playback,
            "status": status,
            "error": error,
            "metadata": metadata,
        }
        with self._lock:
            self._stats["processed"] += 1
            self._last_request_id = request.request_id
            self._last_reply_preview = _short(reply_text)
            self._last_error = ""
            self._last_processed_at = time.time()
        return reply

    async def run_forever(self, *, responder: Responder | None = None) -> None:
        if not self.enabled:
            return
        self.ensure_dirs()
        with self._lock:
            self._running = True
            self._stop_requested = False
        try:
            while True:
                with self._lock:
                    if self._stop_requested:
                        break
                await asyncio.to_thread(self.poll_once, responder=responder)
                await asyncio.sleep(self.poll_seconds)
        except asyncio.CancelledError:
            raise
        finally:
            with self._lock:
                self._running = False

    def request_stop(self) -> None:
        with self._lock:
            self._stop_requested = True

    def _read_request(self, path: Path) -> ExternalBridgeRequest:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("request JSON must be an object")
        return ExternalBridgeRequest.from_payload(payload, path=path)

    def _write_reply(self, request_id: str, reply: dict[str, Any]) -> None:
        self.reply_dir.mkdir(parents=True, exist_ok=True)
        final_path = self.reply_dir / f"{request_id}.json"
        # Lock order: bridge -> session. Publication and forwarded receipts cannot overtake.
        with self._lock:
            old = self._output_records.get(request_id)
            if old is not None and str(reply.get("status", "")).startswith("duplicate"):
                return
            payload = dict(reply)
            updated = None
            if old is not None and old.state == "generated":
                state = "published" if reply.get("ok") and reply.get("reply_text") else "interrupted"
                correlation = {"event_id": old.event_id, "output_id": old.output_id,
                    "attempt_id": old.attempt_id, "platform": old.scope.platform,
                    "room_id": old.scope.room_id, "stream_session_id": old.scope.stream_session_id,
                    "state": state, "revision": old.revision + 1,
                    "timestamp": max(time.time(), old.updated_at)}
                updated = transition_delivery(old, state, old.revision + 1, old.attempt_id, correlation)
                payload["metadata"] = {**reply.get("metadata", {}), "delivery": correlation}
            try:
                _atomic_json_write(final_path, payload)
            except (OSError, ValueError, TypeError):
                if old is not None and old.state == "generated":
                    failed_receipt = {**correlation, "state": "interrupted"}
                    failed = transition_delivery(old, "interrupted", old.revision + 1, old.attempt_id, failed_receipt)
                    self._output_records[request_id] = failed
                    self.social_session.record_reply_context(scope=failed.scope, delivery_record=failed)
                raise
            if updated is not None:
                self._output_records[request_id] = updated
                self.social_session.record_reply_context(scope=updated.scope, delivery_record=updated)
        record_stream_event(
            "reply_json_written",
            request_id=request_id,
            ok=bool(reply.get("ok")),
            status=reply.get("status") or "unknown",
            has_reply=bool(reply.get("reply_text")),
            chars=len(str(reply.get("reply_text") or "")),
            speak=bool(reply.get("speak")),
            reply_file=final_path.name,
        )
        with self._lock:
            self._stats["reply_written"] += 1

    def _move_request(self, path: Path, target_dir: Path) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / path.name
        if target.exists():
            target = target_dir / f"{path.stem}-{int(time.time() * 1000)}{path.suffix}"
        os.replace(path, target)

    def _record_failure(self, path: Path, exc: Exception) -> None:
        with self._lock:
            self._stats["failed"] += 1
            self._last_request_id = path.stem
            self._last_error = f"{type(exc).__name__}: {exc}"
            self._last_processed_at = time.time()
        try:
            self._move_request(path, self.failed_dir)
        except Exception:
            pass
        try:
            self._write_reply(
                path.stem,
                {
                    "request_id": path.stem,
                    "ok": False,
                    "reply_text": "",
                    "speak": False,
                    "audio_paths": [],
                    "audio_target": "discord_text_only",
                    "local_playback": False,
                    "status": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                    "metadata": {"phase": PHASE, "safety": {"local_playback": False, "voice_call": False}},
                },
            )
        except Exception:
            pass

    def _duplicate_reply(self, request: ExternalBridgeRequest, *, local_playback: bool, status: str) -> dict[str, Any]:
        reply = {
            "request_id": request.request_id,
            "ok": True,
            "reply_text": "",
            "speak": False,
            "audio_paths": [],
            "audio_target": request.audio_target or "discord_voice",
            "local_playback": local_playback,
            "status": status,
            "error": None,
            "metadata": {
                "phase": PHASE,
                "source": request.source,
                "event_type": request.event_type,
                "viewer_queue": {
                    "accepted": False,
                    "reason": status,
                    "queued": self.queue.size(),
                    "message_id": None,
                    "priority": "normal",
                    "persona_lane": "public_vtuber_persona",
                    "interaction_scope": "public_viewer",
                    "memory_policy": "public_safe_only_no_private_owner_memory",
                },
                "safety": {
                    "can_act": False,
                    "voice_call": False,
                    "vts_call": False,
                    "obs_call": False,
                    "game_input": False,
                    "local_playback": False,
                },
                "route": request.metadata.get("route") if isinstance(request.metadata.get("route"), dict) else {},
            },
        }
        with self._lock:
            self._stats["processed"] += 1
            self._last_request_id = request.request_id
            self._last_reply_preview = ""
            self._last_error = ""
            self._last_processed_at = time.time()
        return reply

    def _expire_event_keys_locked(self, monotonic_now: float) -> None:
        expired = [
            key
            for key, seen_at in self._seen_event_keys.items()
            if monotonic_now - seen_at > self.event_dedupe_ttl_seconds
        ]
        for key in expired:
            self._seen_event_keys.pop(key, None)


def _atomic_json_write(final_path: Path, payload: dict[str, Any]) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    temp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            delete=False,
            dir=final_path.parent,
            suffix=".tmp",
        ) as handle:
            temp_name = handle.name
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temp_name, final_path)
    finally:
        if temp_name and os.path.exists(temp_name):
            try:
                os.remove(temp_name)
            except OSError:
                pass


def _repair_public_core_self_reply(reply: str, user_text: str) -> str:
    reply_lower = str(reply or "").lower()
    is_service_role_request = _is_service_role_request_text(user_text)
    if not is_service_role_request and not _is_identity_challenge_text(user_text):
        # Stories/factual questions about robots or tools do not challenge Nana's
        # identity. Their content still traverses the normal privacy guards.
        return reply
    if _is_identity_challenge_text(user_text) and any(
        token in reply_lower for token in ("bot discord", "chatbot", "command bot")
    ):
        return _safe_public_identity_boundary_reply()
    try:
        from nana.runtime.core_self import get_core_self
    except Exception:
        return reply
    try:
        result = get_core_self().evaluate_reply(reply, lane="public_stage", prompt=user_text)
    except Exception:
        return reply
    if any(check.kind == "service_tool_identity" and not check.passed for check in result.checks):
        if is_service_role_request:
            return _safe_public_service_boundary_reply()
        return _safe_public_identity_boundary_reply()
    if any(check.kind == "missing_self_stance" and not check.passed for check in result.checks):
        if is_service_role_request:
            return _safe_public_service_boundary_reply()
        return (
            "Nana là Nana chứ. "
            "Có thể là AI, có thể hơi kỳ một chút, nhưng Nana không tự thu nhỏ mình thành cái hộp trả lời lệnh đâu."
        )
    if any(check.kind == "weak_public_service_boundary" and not check.passed for check in result.checks):
        return _safe_public_service_boundary_reply()
    return reply


def _default_public_text_responder(request: ExternalBridgeRequest, *, public_style_hint: str = "") -> str:
    from nana.brain.gpt import ask_gpt, finalize_reply, shape_chat_reply, strip_terminal_audio_tags

    raw = ask_gpt(
        request.text,
        casual_mode=False,
        story_mode=False,
        viewer_name=request.author_name,
        stream_mode=True,
        public_style_hint=public_style_hint,
        metadata={**request.metadata, "platform": request.scope.platform,
                  "room_id": request.scope.room_id,
                  "stream_session_id": request.scope.stream_session_id,
                  "author_id": request.scope.identity.author_id,
                  "event_id": request.scope.event_id} if request.scope else request.metadata,
        **({"public_platform": request.source} if is_livestream_source(request.source) else {}),
    )
    cleaned = finalize_reply(raw or "", casual_mode=False)
    cleaned = shape_chat_reply(cleaned, user_text=request.text, casual_mode=False)
    cleaned = strip_terminal_audio_tags(cleaned)
    return sanitize_public_reply(
        cleaned,
        fallback=public_safe_fallback_for_message(request.text, viewer_name=request.author_name),
        user_text=request.text,
        viewer_name=request.author_name,
    )


_RUNTIME = ExternalBridgeRuntime()
_TASK: asyncio.Task | None = None


def get_external_bridge_runtime() -> ExternalBridgeRuntime:
    return _RUNTIME


def start_external_bridge_worker(loop: asyncio.AbstractEventLoop | None = None) -> asyncio.Task | None:
    global _TASK
    runtime = get_external_bridge_runtime()
    if not runtime.enabled:
        return None
    if _TASK is not None and not _TASK.done():
        return _TASK
    active_loop = loop or asyncio.get_running_loop()
    _TASK = active_loop.create_task(runtime.run_forever())
    return _TASK


def stop_external_bridge_worker() -> asyncio.Task | None:
    """Signal the worker to stop; lifecycle owner decides when to cancel."""
    runtime = get_external_bridge_runtime()
    runtime.request_stop()
    return _TASK


def external_bridge_status_lines() -> list[str]:
    snap = get_external_bridge_runtime().snapshot()
    stats = dict(snap.get("stats") or {})
    social = dict(snap.get("social_session") or {})
    social_stats = dict(social.get("stats") or {})
    social_last = dict(social.get("last_decision") or {})
    age = snap.get("last_processed_age")
    age_text = "never" if age is None else f"{float(age):.1f}s"
    return [
        "🌉 External Bridge Core Worker",
        f"  Mode: {snap.get('phase')} | enabled={snap.get('enabled')} | running={snap.get('running')} | read_only=False",
        f"  Request dir: {snap.get('request_dir')}",
        f"  Reply dir: {snap.get('reply_dir')}",
        f"  Pending: {snap.get('pending')} | poll={float(snap.get('poll_seconds') or 0.0):.2f}s | max_files={snap.get('max_files_per_poll')}",
        (
            "  Stats: "
            f"processed={stats.get('processed', 0)} | queued={stats.get('queued', 0)} | "
            f"reply_written={stats.get('reply_written', 0)} | duplicate={stats.get('duplicate', 0)} | "
            f"rate_limited={stats.get('rate_limited', 0)} | failed={stats.get('failed', 0)}"
        ),
        (
            "  Social decision: "
            f"last={social_last.get('action') or 'none'}:{social_last.get('reason') or 'none'} | "
            f"director={social_last.get('director_mode') or 'none'} | "
            f"velocity={social.get('chat_velocity_per_second', 0.0)}/s | "
            f"full={social_stats.get('full_reply', 0)} | ack={social_stats.get('ack_only', 0)} | "
            f"skip={social_stats.get('skip', 0)}"
        ),
        f"  Last: id={snap.get('last_request_id') or 'none'} | age={age_text} | reply={snap.get('last_reply_preview') or 'none'}",
        f"  Last error: {snap.get('last_error') or 'none'}",
        "  Reply boundary: text_only=True | speak=False | local_playback=False | audio_paths=[]",
        "  Safety: voice_call=False | vts_call=False | obs_call=False | game_input=False",
        "  Live verify: /discord-core-status | send !nana <message> from Discord while Nana is running.",
    ]


def external_bridge_test_report(name: str = "linh", message: str = "hello Nana") -> list[str]:
    request = ExternalBridgeRequest.from_payload(
        {
            "request_id": f"manual-discord-test-{int(time.time() * 1000)}",
            "source": "discord",
            "event_type": "message",
            "text": message,
            "guild_id": None,
            "channel_id": 0,
            "voice_channel_id": None,
            "author_id": 0,
            "author_name": name,
            "audio_target": "discord_voice",
            "local_playback": False,
            "metadata": {"route": {"chat_channel_name": "manual-test", "input_surface": "discord_text"}},
        }
    )
    def _manual_responder(request_obj: ExternalBridgeRequest) -> str:
        return f"Nana nhận được tin nhắn public của {request_obj.author_name}: {request_obj.text}"

    reply = get_external_bridge_runtime().process_request(
        request,
        responder=_manual_responder,
    )
    viewer = dict(reply.get("metadata", {}).get("viewer_queue", {}))
    return [
        "🧪 External Bridge Core Test",
        f"  Input: source=discord | name={name} | message={_short(message)}",
        f"  Reply ok={reply.get('ok')} | status={reply.get('status')} | speak={reply.get('speak')} | local_playback={reply.get('local_playback')}",
        f"  Viewer queue: accepted={viewer.get('accepted')} | reason={viewer.get('reason')} | lane={viewer.get('persona_lane')}",
        f"  Reply text: {_short(reply.get('reply_text'))}",
        "  Safety: voice_call=False | vts_call=False | obs_call=False | game_input=False",
    ]
