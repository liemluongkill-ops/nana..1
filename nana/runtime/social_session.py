"""Session-only public social rhythm for viewer chat.

STAGE-7E-A sits between the public viewer queue and the model call. It keeps
small per-viewer session state, reads public room vibe, adds a lightweight
stage-director mode, and decides whether a message deserves a full reply, a
tiny acknowledgement, or no public echo during fast chat.

No disk persistence, no private owner memory, no TTS/VTS/OBS, no game input.
"""

from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, replace
import re
import threading
import time
from typing import Any, Callable

from nana.runtime.viewer_chat import DEFAULT_PRIORITY_VIEWERS, PRIORITY_PUBLIC_LABEL
from nana.runtime.livestream_identity import is_livestream_source, is_stage_call, mentions_stage_name, stage_prompt_block
from nana.runtime.public_context_boundary import PublicEventScope
from nana.runtime.public_delivery_state import PublicDeliveryRecord, transition_delivery


PHASE = "STAGE-7E-B"
DEFAULT_SESSION_TTL_SECONDS = 60 * 60
DEFAULT_TOPIC_DECAY_SECONDS = 15 * 60
DEFAULT_RECENT_TURNS_MAX = 12
DEFAULT_TOPIC_STACK_MAX = 5
DEFAULT_VELOCITY_WINDOW_SECONDS = 20.0
DEFAULT_MEDIUM_VELOCITY_PER_SECOND = 0.35
DEFAULT_HIGH_VELOCITY_PER_SECOND = 0.70
DEFAULT_LIGHT_REACTION_ACK_COOLDOWN_SECONDS = 30.0

ACTION_FULL_REPLY = "full_reply"
ACTION_ACK_ONLY = "ack_only"
ACTION_SKIP = "skip"

EVENT_TEXT = "text"
EVENT_QUESTION = "question"
EVENT_STORY_REQUEST = "story_request"
EVENT_GREETING = "greeting"
EVENT_EMOJI_ONLY = "emoji_only"
EVENT_STICKER = "sticker"
EVENT_EMPTY = "empty"
EVENT_IDENTITY_CHALLENGE = "identity_challenge"

ACK_LINES = {
    "seen": "Nana thấy rồi nè.",
    "fast": "Nana đọc kịp rồi, để mình bắt nhịp đã nha.",
    "emoji_priority": "Nana thấy rồi, dễ thương đó nha.",
}


def _clean_text(value: Any, limit: int = 500) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _preview(value: Any, limit: int = 96) -> str:
    text = _clean_text(value, limit=limit + 1)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def _fold_for_similarity(value: Any) -> str:
    text = _clean_text(value, limit=240).lower()
    text = re.sub(r"^!nana\s+", "", text)
    text = re.sub(r"[^\w\sÀ-ỹ]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _token_similarity(left: Any, right: Any) -> float:
    left_tokens = set(_fold_for_similarity(left).split())
    right_tokens = set(_fold_for_similarity(right).split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens)
    return overlap / max(1, min(len(left_tokens), len(right_tokens)))


def _similar_recent_turns(turns: list["PublicTurn"], topic: Any, *, limit: int = 6) -> list["PublicTurn"]:
    folded = _fold_for_similarity(topic)
    if not folded:
        return []
    return [
        turn
        for turn in turns[-limit:]
        if turn.reply_preview and _token_similarity(folded, turn.message_preview) >= 0.72
    ]


def _normalize_viewer_name(value: Any) -> str:
    text = _clean_text(value).lower()
    text = re.sub(r"^<@!?", "", text)
    text = re.sub(r">$", "", text)
    text = re.sub(r"#\d{4}$", "", text)
    text = text.lstrip("@")
    return re.sub(r"\s+", "", text)


def _prompt_display_name(value: Any) -> str:
    """Render an untrusted display name without allowing prompt-label injection."""
    text = _clean_text(value or "viewer", 48)
    text = re.sub(r"[\[\];=<>\r\n]", "_", text).replace('"', "'").replace("\\", "_")
    text = re.sub(
        r"speaker[_ -]?ref|display[_ -]?name|current[_ -]?viewer|other[_ -]?viewer|unverified[_ -]?viewer",
        "viewer",
        text,
        flags=re.IGNORECASE,
    )
    return text or "viewer"


def _viewer_key(platform: Any, channel: Any, viewer_name: Any) -> str:
    platform_clean = _clean_text(platform or "discord").lower()
    channel_clean = _clean_text(channel or "public").lower()
    viewer_clean = _normalize_viewer_name(viewer_name) or "viewer"
    return f"{platform_clean}:{channel_clean}:{viewer_clean}"


def _is_emoji_only(text: str) -> bool:
    compact = re.sub(r"\s+", "", text or "")
    if not compact:
        return False
    if re.fullmatch(r"<a?:[A-Za-z0-9_]{1,64}:\d{10,32}>", compact):
        return True
    # Vietnamese/CJK letters and digits are alnum; pure emoji/sticker reactions
    # usually have no alphanumeric characters and at least one non-ASCII symbol.
    return not any(ch.isalnum() for ch in compact) and any(ord(ch) > 127 for ch in compact)


def classify_social_event(text: Any, *, event_type: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    raw = _clean_text(text)
    event = str(event_type or "").strip().lower()
    meta = metadata if isinstance(metadata, dict) else {}
    route = meta.get("route") if isinstance(meta.get("route"), dict) else {}
    has_media = bool(meta.get("attachments") or meta.get("stickers") or route.get("input_has_media"))
    if event in {"sticker", "sticker_create", "sticker_update"} or meta.get("stickers"):
        return EVENT_STICKER
    if not raw:
        return EVENT_STICKER if has_media else EVENT_EMPTY
    lowered = raw.lower()
    if is_public_identity_challenge(raw):
        return EVENT_IDENTITY_CHALLENGE
    if _is_emoji_only(raw):
        return EVENT_EMOJI_ONLY
    if re.search(r"(^|\s)(chào|hello|hi|hey|alo|yo)(\s|[!.?,]|$)", lowered):
        return EVENT_GREETING
    if any(token in lowered for token in ("kể chuyện", "chuyện dài", "story", "kể dài")):
        return EVENT_STORY_REQUEST
    if "?" in raw or any(token in lowered for token in ("không", "ko", "là gì", "sao", "như nào", "thế nào")):
        return EVENT_QUESTION
    return EVENT_TEXT


def is_public_identity_challenge(text: Any) -> bool:
    lowered = _clean_text(text, limit=500).lower()
    if "nana" not in lowered:
        return False
    flattens = any(marker in lowered for marker in (
        "bot discord",
        "chatbot",
        "command bot",
        "trợ lý",
        "assistant",
        "công cụ",
        "tool",
        "hộp trả lời",
        "máy trả lời",
    ))
    asks = "?" in lowered or any(marker in lowered for marker in (
        "đúng không",
        "phải không",
        "thôi",
        "chỉ là",
        "có phải",
        "là bot",
    ))
    return flattens and asks


def infer_topic(text: Any, event_type: str) -> str:
    raw = _clean_text(text, limit=140)
    lowered = raw.lower()
    if event_type == EVENT_STORY_REQUEST:
        return "story_request"
    if event_type == EVENT_IDENTITY_CHALLENGE:
        return "identity_challenge"
    if "memory" in lowered or "bộ nhớ" in lowered or "nhớ" in lowered:
        return "memory"
    if "code" in lowered or "lập trình" in lowered:
        return "code"
    if "discord" in lowered or "kênh chat" in lowered:
        return "discord_chat"
    if "ăn" in lowered or "đồ ăn" in lowered:
        return "food"
    if raw:
        return raw[:60]
    return ""


@dataclass
class PublicTurn:
    timestamp: float
    monotonic: float
    viewer_name: str
    event_type: str
    director_mode: str
    message_preview: str
    reply_preview: str = ""
    action: str = ""
    topic: str = ""
    scope: PublicEventScope | None = None
    revision: int = 0
    attempt_id: str = ""
    delivery_state: str = "generated"

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.scope is not None:
            data["actor_key"] = self.scope.identity.actor_key
            data["event_id"] = self.scope.event_id
        return data


# Conversation/session data, deliberately distinct from durable MemoryRecord.
ConversationEvent = PublicTurn


@dataclass
class SocialViewerState:
    viewer_key: str
    display_name: str
    platform: str
    channel: str
    first_seen: float
    last_seen: float
    first_seen_monotonic: float
    last_seen_monotonic: float
    message_count: int = 0
    priority_score: int = 1
    greeted_once: bool = False
    last_topic: str = ""
    last_topic_at: float = 0.0
    mood_hint: str = "neutral"
    mood_at: float = 0.0
    last_reply_at: float = 0.0
    last_light_ack_monotonic: float = 0.0

    def to_dict(self, *, now: float, monotonic_now: float, topic_decay_seconds: float) -> dict[str, Any]:
        data = asdict(self)
        data["age_seconds"] = max(0.0, now - self.first_seen)
        data["last_seen_age_seconds"] = max(0.0, now - self.last_seen)
        if self.last_topic_at and monotonic_now - self.last_topic_at > topic_decay_seconds:
            data["last_topic"] = ""
            data["topic_decayed"] = True
        else:
            data["topic_decayed"] = False
        return data


@dataclass(frozen=True)
class SocialDecision:
    action: str
    reason: str
    event_type: str
    chat_velocity: float
    priority_score: int
    should_call_llm: bool
    reply_text: str = ""
    familiarity: str = "new_viewer"
    room_vibe: str = "quiet_room"
    director_mode: str = "chill"
    director_risk: str = "low"
    response_shape: str = "natural_reply"
    style_hint: str = ""
    can_act: bool = False
    voice_call: bool = False
    vts_call: bool = False
    obs_call: bool = False
    game_input: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chat_velocity"] = round(float(self.chat_velocity), 3)
        return data


class SocialSessionCache:
    """In-memory public social session state and reply budget."""

    def __init__(
        self,
        *,
        session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        topic_decay_seconds: float = DEFAULT_TOPIC_DECAY_SECONDS,
        velocity_window_seconds: float = DEFAULT_VELOCITY_WINDOW_SECONDS,
        medium_velocity_per_second: float = DEFAULT_MEDIUM_VELOCITY_PER_SECOND,
        high_velocity_per_second: float = DEFAULT_HIGH_VELOCITY_PER_SECOND,
        light_reaction_ack_cooldown_seconds: float = DEFAULT_LIGHT_REACTION_ACK_COOLDOWN_SECONDS,
        priority_viewers: tuple[str, ...] | list[str] | set[str] | None = None,
        clock: Callable[[], float] | None = None,
        _bound_scope: PublicEventScope | None = None,
    ) -> None:
        self.session_ttl_seconds = float(session_ttl_seconds)
        self.topic_decay_seconds = float(topic_decay_seconds)
        self.velocity_window_seconds = float(velocity_window_seconds)
        self.medium_velocity_per_second = float(medium_velocity_per_second)
        self.high_velocity_per_second = float(high_velocity_per_second)
        self.light_reaction_ack_cooldown_seconds = float(light_reaction_ack_cooldown_seconds)
        raw_priority = DEFAULT_PRIORITY_VIEWERS if priority_viewers is None else priority_viewers
        self.priority_viewers = {
            normalized
            for normalized in (_normalize_viewer_name(name) for name in raw_priority)
            if normalized
        }
        self._lock = threading.Lock()
        self._viewers: dict[str, SocialViewerState] = {}
        self._events: deque[tuple[float, str, str]] = deque()
        self._recent_turns: deque[PublicTurn] = deque(maxlen=DEFAULT_RECENT_TURNS_MAX)
        self._topic_stack: deque[str] = deque(maxlen=DEFAULT_TOPIC_STACK_MAX)
        self._last_addressed_viewer = ""
        self._skipped_light_reactions = 0
        self._stats = {
            "observed": 0,
            "full_reply": 0,
            "ack_only": 0,
            "skip": 0,
            "priority_full_reply": 0,
            "emoji_or_sticker": 0,
            "light_reaction_cooldown_skip": 0,
            "expired_viewers": 0,
        }
        self._last_decision: SocialDecision | None = None
        self._last_viewer_key = ""
        self._clock = clock
        self._bound_scope = _bound_scope
        self._sessions: dict[tuple[str, str, str], SocialSessionCache] = {}
        self._active_sessions: dict[tuple[str, str], str] = {}
        self._delivery: dict[tuple, PublicDeliveryRecord] = {}
        self._last_public_eval = {"grade": "none", "issue_kinds": []}

    def _partition(self, scope: PublicEventScope) -> "SocialSessionCache":
        if not isinstance(scope, PublicEventScope):
            raise ValueError("canonical public scope required")
        key = (scope.platform, scope.room_id, scope.stream_session_id)
        if self._bound_scope is not None:
            own = (self._bound_scope.platform, self._bound_scope.room_id, self._bound_scope.stream_session_id)
            if key != own:
                raise ValueError("public room/session mismatch")
            return self
        with self._lock:
            existing = self._sessions.get(key)
            if existing is None:
                existing = SocialSessionCache(
                    session_ttl_seconds=self.session_ttl_seconds,
                    topic_decay_seconds=self.topic_decay_seconds,
                    velocity_window_seconds=self.velocity_window_seconds,
                    medium_velocity_per_second=self.medium_velocity_per_second,
                    high_velocity_per_second=self.high_velocity_per_second,
                    light_reaction_ack_cooldown_seconds=self.light_reaction_ack_cooldown_seconds,
                    priority_viewers=self.priority_viewers, clock=self._clock, _bound_scope=scope,
                )
                # Bound the facade too; empty/old partitions cannot grow forever.
                if len(self._sessions) >= 128:
                    self._sessions.pop(next(iter(self._sessions)))
                self._sessions[key] = existing
            return existing

    def start_session(self, scope: PublicEventScope) -> None:
        if self._bound_scope is not None:
            raise ValueError("session lifecycle belongs to the cache facade")
        room = (scope.platform, scope.room_id)
        with self._lock:
            for key in list(self._sessions):
                if key[:2] == room and key[2] != scope.stream_session_id:
                    del self._sessions[key]
            self._active_sessions[room] = scope.stream_session_id
        self._partition(scope)

    def reset_session(self, session_key: str) -> None:
        with self._lock:
            for key, cache in list(self._sessions.items()):
                if cache._bound_scope.session_key == session_key:
                    del self._sessions[key]

    def durable_viewer_keys(self) -> tuple[str, ...]:
        """Phase 1 has no durable viewer store, including anonymous viewers."""
        return ()

    def record_evaluation(self, scope: PublicEventScope, evaluation: dict) -> None:
        cache = self._partition(scope)
        with cache._lock:
            cache._last_public_eval = {"grade": str(evaluation.get("grade", "none")),
                                      "issue_kinds": list(evaluation.get("issue_kinds") or [])}

    def feedback_mode(self, scope: PublicEventScope, text: str) -> str:
        from nana.runtime.public_reply_feedback import PublicReplyFeedback
        cache = self._partition(scope)
        with cache._lock:
            evaluation = dict(cache._last_public_eval)
        return PublicReplyFeedback().build(text=text, last_eval=evaluation).mode

    def record_public_turn(self, *, scope: PublicEventScope, text: str, topic: str = "",
                           revision: int = 0, attempt_id: str = "") -> PublicTurn:
        cache = self._partition(scope)
        if cache is not self:
            return cache.record_public_turn(scope=scope, text=text, topic=topic,
                                           revision=revision, attempt_id=attempt_id)
        if type(revision) is not int or revision < 0:
            raise ValueError("nonnegative event revision required")
        now = self._clock() if self._clock else time.time()
        mono = self._clock() if self._clock else time.monotonic()
        with self._lock:
            self._expire_locked(now, mono)
            for turn in self._recent_turns:
                if turn.scope and turn.scope.event_id == scope.event_id:
                    # An input event is immutable; retries belong to output attempts.
                    return turn
            turn = PublicTurn(now, mono, scope.display_name, "text", "chill",
                              _preview(text, 96), topic=_clean_text(topic, 60),
                              scope=scope, revision=revision, attempt_id=attempt_id)
            self._recent_turns.append(turn)
            if turn.topic:
                self._topic_stack.append(turn.topic)
            return turn

    def clear(self) -> dict[str, Any]:
        with self._lock:
            viewers = len(self._viewers)
            events = len(self._events)
            turns = len(self._recent_turns)
            self._viewers.clear()
            self._events.clear()
            self._recent_turns.clear()
            self._topic_stack.clear()
            self._last_decision = None
            self._last_viewer_key = ""
            self._last_addressed_viewer = ""
            self._skipped_light_reactions = 0
            self._sessions.clear()
            self._active_sessions.clear()
            self._delivery.clear()
            self._last_public_eval = {"grade": "none", "issue_kinds": []}
            for key in self._stats:
                self._stats[key] = 0
            return {"cleared_viewers": viewers, "cleared_events": events, "cleared_turns": turns, "can_act": False}

    def observe(
        self,
        *,
        platform: str = "discord",
        channel: str = "public",
        viewer_name: str = "viewer",
        text: str = "",
        event_type: str = "message",
        priority: str = "normal",
        metadata: dict[str, Any] | None = None,
        now: float | None = None,
        monotonic_now: float | None = None,
        scope: PublicEventScope | None = None,
    ) -> SocialDecision:
        if scope is not None:
            cache = self._partition(scope)
            if cache is not self:
                return cache.observe(platform=scope.platform, channel=scope.room_id,
                                     viewer_name=scope.display_name, text=text, event_type=event_type,
                                     priority=priority, metadata=metadata, now=now,
                                     monotonic_now=monotonic_now, scope=scope)
        current = (self._clock() if self._clock else time.time()) if now is None else float(now)
        current_mono = (self._clock() if self._clock else time.monotonic()) if monotonic_now is None else float(monotonic_now)
        platform_clean = _clean_text(platform or "discord").lower()
        channel_clean = _clean_text(channel or "public")
        name_clean = _clean_text(viewer_name or "viewer")
        key = scope.identity.actor_key if scope is not None else _viewer_key(platform_clean, channel_clean, name_clean)
        priority_score = self.priority_score_for(name_clean, priority=priority)
        social_event = classify_social_event(text, event_type=event_type, metadata=metadata)
        if is_livestream_source(platform_clean) and is_stage_call(text):
            social_event = EVENT_GREETING

        with self._lock:
            self._expire_locked(current, current_mono)
            state = self._viewers.get(key)
            if state is None:
                state = SocialViewerState(
                    viewer_key=key,
                    display_name=name_clean,
                    platform=platform_clean,
                    channel=channel_clean,
                    first_seen=current,
                    last_seen=current,
                    first_seen_monotonic=current_mono,
                    last_seen_monotonic=current_mono,
                    priority_score=priority_score,
                )
                self._viewers[key] = state
            state.last_seen = current
            state.last_seen_monotonic = current_mono
            state.display_name = name_clean
            state.message_count += 1
            state.priority_score = max(state.priority_score, priority_score)
            if social_event == EVENT_GREETING:
                state.greeted_once = True
            topic = infer_topic(text, social_event)
            if topic:
                state.last_topic = topic
                state.last_topic_at = current_mono
            if social_event in {EVENT_EMOJI_ONLY, EVENT_STICKER}:
                self._stats["emoji_or_sticker"] += 1

            self._events.append((current_mono, key, social_event))
            self._expire_events_locked(current_mono)
            velocity = self._chat_velocity_locked(current_mono)
            familiarity = self._viewer_familiarity(state, priority_score=priority_score)
            room_vibe = self._room_vibe(velocity)
            director = self._stage_director(
                text=text,
                event_type=social_event,
                familiarity=familiarity,
                room_vibe=room_vibe,
                priority_score=priority_score,
            )
            style_hint = self._build_style_hint(
                state=state,
                text=text,
                event_type=social_event,
                familiarity=familiarity,
                room_vibe=room_vibe,
                priority_score=priority_score,
                director=director,
            )
            decision = self._decide_locked(
                event_type=social_event,
                chat_velocity=velocity,
                priority_score=priority_score,
                state=state,
                monotonic_now=current_mono,
                familiarity=familiarity,
                room_vibe=room_vibe,
                director_mode=director["mode"],
                director_risk=director["risk"],
                response_shape=director["response_shape"],
                style_hint=style_hint,
            )
            if decision.action == ACTION_FULL_REPLY:
                state.last_reply_at = current
            if decision.action == ACTION_ACK_ONLY and social_event in {EVENT_EMOJI_ONLY, EVENT_STICKER}:
                state.last_light_ack_monotonic = current_mono
            self._stats["observed"] += 1
            self._stats[decision.action] += 1
            if decision.reason == "priority_light_reaction_cooldown":
                self._stats["light_reaction_cooldown_skip"] += 1
                self._skipped_light_reactions += 1
            if priority_score >= 5 and decision.action == ACTION_FULL_REPLY:
                self._stats["priority_full_reply"] += 1
            if decision.action == ACTION_FULL_REPLY and state.display_name:
                self._last_addressed_viewer = state.display_name
            elif (decision.action == ACTION_SKIP
                  and social_event in {EVENT_EMOJI_ONLY, EVENT_STICKER}
                  and decision.reason != "priority_light_reaction_cooldown"):
                self._skipped_light_reactions += 1
            self._last_decision = decision
            self._last_viewer_key = key
            return decision

    def priority_score_for(self, viewer_name: Any, *, priority: str = "normal") -> int:
        if priority == PRIORITY_PUBLIC_LABEL or _normalize_viewer_name(viewer_name) in self.priority_viewers:
            return 5
        return 2

    def snapshot(self, *, now: float | None = None, monotonic_now: float | None = None,
                 scope: PublicEventScope | None = None) -> dict[str, Any]:
        if scope is not None:
            cache = self._partition(scope)
            if cache is not self:
                return cache.snapshot(now=now, monotonic_now=monotonic_now)
        current = (self._clock() if self._clock else time.time()) if now is None else float(now)
        current_mono = (self._clock() if self._clock else time.monotonic()) if monotonic_now is None else float(monotonic_now)
        with self._lock:
            self._expire_locked(current, current_mono)
            velocity = self._chat_velocity_locked(current_mono)
            viewers = [
                state.to_dict(now=current, monotonic_now=current_mono, topic_decay_seconds=self.topic_decay_seconds)
                for state in self._viewers.values()
            ]
            viewers.sort(key=lambda item: item.get("last_seen", 0.0), reverse=True)
            last_decision = self._last_decision.to_dict() if self._last_decision else None
            recent_turns = [t.to_dict() for t in self._recent_turns]
            topic_stack = list(self._topic_stack)
            return {
                "mode": PHASE,
                "read_only": True,
                "can_act": False,
                "viewer_count": len(viewers),
                "event_count": len(self._events),
                "chat_velocity_per_second": round(float(velocity), 3),
                "velocity_window_seconds": self.velocity_window_seconds,
                "medium_velocity_per_second": self.medium_velocity_per_second,
                "high_velocity_per_second": self.high_velocity_per_second,
                "light_reaction_ack_cooldown_seconds": self.light_reaction_ack_cooldown_seconds,
                "topic_decay_seconds": self.topic_decay_seconds,
                "session_ttl_seconds": self.session_ttl_seconds,
                "priority_viewers": sorted(self.priority_viewers),
                "recent_turns_count": len(self._recent_turns),
                "recent_turns": recent_turns,
                "room_topic": topic_stack[-1] if topic_stack else None,
                "topic_stack": topic_stack,
                "last_addressed_viewer": self._last_addressed_viewer or None,
                "skipped_light_reactions": self._skipped_light_reactions,
                "stats": dict(self._stats),
                "last_decision": last_decision,
                "last_viewer": viewers[0] if viewers else None,
                "viewers": viewers[:8],
                "safety": {
                    "can_act": False,
                    "voice_call": False,
                    "vts_call": False,
                    "obs_call": False,
                    "game_input": False,
                    "memory_persistence": False,
                },
            }

    def get_temperature_state(self, *, now: float | None = None, monotonic_now: float | None = None) -> dict[str, Any]:
        """Return the public room's social temperature.

        This is the single social-temperature source for STAGE-9A/9B. Mood
        continuity may reference this object, but it must not infer room state
        from Nana's internal warmth/energy axes.
        """
        current = time.time() if now is None else float(now)
        current_mono = time.monotonic() if monotonic_now is None else float(monotonic_now)
        with self._lock:
            self._expire_locked(current, current_mono)
            events = list(self._events)
            velocity = self._chat_velocity_locked(current_mono)
            viewer_count = len(self._viewers)
            event_count = len(events)
            stats = dict(self._stats)
            last_decision = self._last_decision.to_dict() if self._last_decision else None
            last_event_mono = None
            if events:
                last_event_mono = max(item[0] for item in events)
            elif self._viewers:
                last_event_mono = max(state.last_seen_monotonic for state in self._viewers.values())

        velocity_per_minute = round(float(velocity) * 60.0, 2)
        last_event_age_seconds = (
            round(max(0.0, current_mono - last_event_mono), 1)
            if last_event_mono is not None
            else None
        )

        if event_count >= 3:
            half = max(1.0, self.velocity_window_seconds / 2.0)
            older = [event for event in events if current_mono - event[0] > half]
            recent = [event for event in events if current_mono - event[0] <= half]
            older_rate = len(older) / half
            recent_rate = len(recent) / half
            if recent_rate > older_rate * 1.25 + 0.02:
                trend = "rising"
            elif recent_rate < max(0.01, older_rate * 0.70):
                trend = "cooling"
            else:
                trend = "stable"
        else:
            trend = "unknown"

        if event_count == 0 and last_event_mono is None:
            state = "unknown"
            confidence = 0.0
        elif velocity >= self.high_velocity_per_second:
            state = "fast"
            confidence = 0.90
        elif velocity >= self.medium_velocity_per_second:
            state = "active"
            confidence = 0.80
        elif event_count >= 3 and trend == "cooling":
            state = "cooling"
            confidence = 0.70
        elif event_count >= 3 and trend in {"stable", "rising"}:
            state = "stable"
            confidence = 0.65
        elif event_count == 0 and last_event_age_seconds is not None and last_event_age_seconds >= self.velocity_window_seconds:
            state = "dormant"
            confidence = 0.55
        else:
            state = "unknown"
            confidence = 0.25 if event_count else 0.10

        if event_count >= 2:
            unique_viewers = len({key for _, key, _ in events})
            viewer_participation = round(unique_viewers / max(1, event_count), 2)
        else:
            viewer_participation = "unknown"

        if event_count >= 3:
            emoji_events = sum(1 for _, _, event in events if event in {EVENT_EMOJI_ONLY, EVENT_STICKER})
            text_events = sum(1 for _, _, event in events if event not in {EVENT_EMOJI_ONLY, EVENT_STICKER, EVENT_EMPTY})
            emoji_ratio = round(emoji_events / max(1, event_count), 2)
            text_ratio = round(text_events / max(1, event_count), 2)
            if emoji_ratio >= 0.60:
                engagement_depth = "light_reactions"
            elif text_ratio >= 0.60:
                engagement_depth = "textual"
            else:
                engagement_depth = "mixed"
        else:
            emoji_ratio = "unknown"
            text_ratio = "unknown"
            engagement_depth = "unknown"

        return {
            "source": "social_session",
            "state": state,
            "confidence": round(confidence, 2),
            "trend": trend,
            "chat_velocity_per_second": round(float(velocity), 3),
            "chat_velocity_per_minute": velocity_per_minute,
            "viewer_count": viewer_count,
            "event_count": event_count,
            "last_event_age_seconds": last_event_age_seconds,
            "viewer_participation": viewer_participation,
            "engagement_depth": engagement_depth,
            "emoji_ratio": emoji_ratio,
            "text_ratio": text_ratio,
            "room_vibe": (last_decision or {}).get("room_vibe") or self._room_vibe(velocity),
            "stats": {
                "full_reply": stats.get("full_reply", 0),
                "ack_only": stats.get("ack_only", 0),
                "skip": stats.get("skip", 0),
                "emoji_or_sticker": stats.get("emoji_or_sticker", 0),
            },
            "read_only": True,
            "can_act": False,
        }

    def _decide_locked(
        self,
        *,
        event_type: str,
        chat_velocity: float,
        priority_score: int,
        state: SocialViewerState,
        monotonic_now: float,
        familiarity: str,
        room_vibe: str,
        director_mode: str,
        director_risk: str,
        response_shape: str,
        style_hint: str,
    ) -> SocialDecision:
        is_priority = priority_score >= 5
        if event_type in {EVENT_EMPTY}:
            return SocialDecision(
                action=ACTION_SKIP,
                reason="empty_event",
                event_type=event_type,
                chat_velocity=chat_velocity,
                priority_score=priority_score,
                should_call_llm=False,
                familiarity=familiarity,
                room_vibe=room_vibe,
                director_mode=director_mode,
                director_risk=director_risk,
                response_shape=response_shape,
                style_hint=style_hint,
            )
        if event_type == EVENT_IDENTITY_CHALLENGE:
            return SocialDecision(
                action=ACTION_FULL_REPLY,
                reason="identity_challenge",
                event_type=event_type,
                chat_velocity=chat_velocity,
                priority_score=priority_score,
                should_call_llm=True,
                familiarity=familiarity,
                room_vibe=room_vibe,
                director_mode="answer",
                director_risk="low",
                response_shape="identity_boundary",
                style_hint=style_hint,
            )
        if event_type in {EVENT_EMOJI_ONLY, EVENT_STICKER}:
            if is_priority:
                if (
                    state.last_light_ack_monotonic
                    and monotonic_now - state.last_light_ack_monotonic < self.light_reaction_ack_cooldown_seconds
                ):
                    return SocialDecision(
                        action=ACTION_SKIP,
                        reason="priority_light_reaction_cooldown",
                        event_type=event_type,
                        chat_velocity=chat_velocity,
                        priority_score=priority_score,
                        should_call_llm=False,
                        familiarity=familiarity,
                        room_vibe=room_vibe,
                        director_mode=director_mode,
                        director_risk=director_risk,
                        response_shape=response_shape,
                        style_hint=style_hint,
                    )
                return SocialDecision(
                    action=ACTION_ACK_ONLY,
                    reason="priority_light_reaction",
                    event_type=event_type,
                    chat_velocity=chat_velocity,
                    priority_score=priority_score,
                    should_call_llm=False,
                    reply_text=ACK_LINES["emoji_priority"],
                    familiarity=familiarity,
                    room_vibe=room_vibe,
                    director_mode=director_mode,
                    director_risk=director_risk,
                    response_shape=response_shape,
                    style_hint=style_hint,
                )
            return SocialDecision(
                action=ACTION_SKIP,
                reason="light_reaction_no_llm",
                event_type=event_type,
                chat_velocity=chat_velocity,
                priority_score=priority_score,
                should_call_llm=False,
                familiarity=familiarity,
                room_vibe=room_vibe,
                director_mode=director_mode,
                director_risk=director_risk,
                response_shape=response_shape,
                style_hint=style_hint,
            )
        if is_priority:
            return SocialDecision(
                action=ACTION_FULL_REPLY,
                reason="priority_viewer",
                event_type=event_type,
                chat_velocity=chat_velocity,
                priority_score=priority_score,
                should_call_llm=True,
                familiarity=familiarity,
                room_vibe=room_vibe,
                director_mode=director_mode,
                director_risk=director_risk,
                response_shape=response_shape,
                style_hint=style_hint,
            )
        if chat_velocity >= self.high_velocity_per_second:
            return SocialDecision(
                action=ACTION_SKIP,
                reason="chat_velocity_high",
                event_type=event_type,
                chat_velocity=chat_velocity,
                priority_score=priority_score,
                should_call_llm=False,
                familiarity=familiarity,
                room_vibe=room_vibe,
                director_mode=director_mode,
                director_risk=director_risk,
                response_shape=response_shape,
                style_hint=style_hint,
            )
        if chat_velocity >= self.medium_velocity_per_second and event_type not in {
            EVENT_QUESTION,
            EVENT_IDENTITY_CHALLENGE,
            EVENT_STORY_REQUEST,
            EVENT_GREETING,
        }:
            return SocialDecision(
                action=ACTION_ACK_ONLY,
                reason="chat_velocity_medium",
                event_type=event_type,
                chat_velocity=chat_velocity,
                priority_score=priority_score,
                should_call_llm=False,
                reply_text=ACK_LINES["fast"],
                familiarity=familiarity,
                room_vibe=room_vibe,
                director_mode=director_mode,
                director_risk=director_risk,
                response_shape=response_shape,
                style_hint=style_hint,
            )
        return SocialDecision(
            action=ACTION_FULL_REPLY,
            reason="normal_chat",
            event_type=event_type,
            chat_velocity=chat_velocity,
            priority_score=priority_score,
            should_call_llm=True,
            familiarity=familiarity,
            room_vibe=room_vibe,
            director_mode=director_mode,
            director_risk=director_risk,
            response_shape=response_shape,
            style_hint=style_hint,
        )

    def _viewer_familiarity(self, state: SocialViewerState, *, priority_score: int) -> str:
        if priority_score >= 5:
            return "priority_viewer"
        if state.message_count <= 1:
            return "new_viewer"
        if state.message_count <= 3:
            return "returning_session_viewer"
        return "familiar_session_viewer"

    def _room_vibe(self, chat_velocity: float) -> str:
        if chat_velocity >= self.high_velocity_per_second:
            return "fast_chat"
        if chat_velocity >= self.medium_velocity_per_second:
            return "active_chat"
        return "quiet_room"

    def _stage_director(
        self,
        *,
        text: Any,
        event_type: str,
        familiarity: str,
        room_vibe: str,
        priority_score: int,
    ) -> dict[str, str]:
        lowered = _clean_text(text, limit=500).lower()
        toxic_markers = (
            "đm",
            "dm ",
            "địt",
            "cút",
            "ngu",
            "óc chó",
            "toxic",
            "chửi",
            "cãi nhau",
            "ghét nhau",
            "drama",
        )
        repair_markers = (
            "bug",
            "lỗi",
            "bị lệch",
            "trả lời lệch",
            "không tự nhiên",
            "máy móc",
            "support bot",
            "spam",
            "chán",
        )
        playful_markers = (
            "haha",
            "hihi",
            "kk",
            "cute",
            "dễ thương",
            "khịa",
            "trêu",
            "vui",
            "hài",
        )
        if any(marker in lowered for marker in toxic_markers):
            return {"mode": "mediator", "risk": "medium", "response_shape": "calm_redirect"}
        if any(marker in lowered for marker in repair_markers):
            return {"mode": "repair", "risk": "low", "response_shape": "acknowledge_adjust"}
        if event_type == EVENT_STORY_REQUEST:
            return {"mode": "storyteller", "risk": "low", "response_shape": "medium_story"}
        if event_type == EVENT_IDENTITY_CHALLENGE:
            return {"mode": "answer", "risk": "low", "response_shape": "identity_boundary"}
        if event_type == EVENT_QUESTION:
            return {"mode": "answer", "risk": "low", "response_shape": "answer_first"}
        if event_type == EVENT_GREETING:
            return {"mode": "greeter", "risk": "low", "response_shape": "warm_short_greeting"}
        if event_type in {EVENT_EMOJI_ONLY, EVENT_STICKER}:
            return {"mode": "reactor", "risk": "low", "response_shape": "light_reaction"}
        if any(marker in lowered for marker in playful_markers):
            return {"mode": "witty", "risk": "low", "response_shape": "playful_reply"}
        if room_vibe == "fast_chat" and priority_score < 5:
            return {"mode": "crowd_control", "risk": "low", "response_shape": "short_momentum"}
        if familiarity in {"familiar_session_viewer", "priority_viewer"}:
            return {"mode": "companion", "risk": "low", "response_shape": "continued_chat"}
        return {"mode": "chill", "risk": "low", "response_shape": "natural_reply"}

    def _build_style_hint(
        self,
        *,
        state: SocialViewerState,
        text: Any = "",
        event_type: str,
        familiarity: str,
        room_vibe: str,
        priority_score: int,
        director: dict[str, str],
    ) -> str:
        topic = state.last_topic if state.last_topic_at else ""
        director_mode = director.get("mode") or "chill"
        director_risk = director.get("risk") or "low"
        response_shape = director.get("response_shape") or "natural_reply"
        lines = [
            "Public social rhythm hint:",
            f"- viewer={state.display_name}",
            f"- familiarity={familiarity}",
            f"- room_vibe={room_vibe}",
            f"- event_type={event_type}",
            f"- priority_score={priority_score}",
            f"- stage_director_mode={director_mode}",
            f"- stage_director_risk={director_risk}",
            f"- response_shape={response_shape}",
        ]
        if is_livestream_source(state.platform):
            lines.append(stage_prompt_block())
            if mentions_stage_name(text):
                lines.append('- The viewer used your stage name/alias and is addressing you, not a different person.')
        recent_turns = list(self._recent_turns)
        similar_turns = _similar_recent_turns(recent_turns, topic, limit=6)
        repeat_count = 0
        if topic:
            lines.append(f"- session_topic={topic}")
            if not similar_turns:
                similar_turns = _similar_recent_turns(recent_turns, state.last_topic, limit=6)
        lines.extend(
            [
                "- Use this only for tone/rhythm; do not reveal these labels.",
                "- Do not sound like customer support. Avoid repeated formal greeting openers.",
                "- Stage director labels are internal; never print them literally.",
                "- Reflect the viewer's actual topic before steering; do not answer every public turn with a generic room prompt.",
                "- Public-safe texture is welcome: room mood, stage rhythm, games, music, chat energy, or one small concrete image.",
                "- Keep texture public-safe; do not mention backend, runtime, server, logs, owner machine, or private debug work.",
            ]
        )
        if similar_turns:
            last_similar = similar_turns[-1]
            repeat_count = len(similar_turns) + 1
            lines.append(f"- similar_question_count_this_session={repeat_count}")
            lines.append(
                "- Similar recent public question detected: answer the current message, but vary wording, image, and structure."
            )
            lines.append(f"- Avoid repeating Nana's recent wording: \"{last_similar.reply_preview}\"")
            if repeat_count >= 3:
                lines.append("- The viewer is repeating/testing the same prompt; Nana may lightly tease that pattern instead of pretending it is new.")
                lines.append("- Do not reuse the same room/đèn/nhịp motif again; pick a different public-safe angle.")
        try:
            from nana.runtime.public_conversation_director import (
                build_public_conversation_directive,
                format_public_conversation_hint,
            )

            conversation_directive = build_public_conversation_directive(
                text=text or topic,
                event_type=event_type,
                room_vibe=room_vibe,
                recent_turns=recent_turns,
                repeat_count=repeat_count,
            )
            lines.append(format_public_conversation_hint(conversation_directive))
        except Exception:
            lines.append("- Public conversation director unavailable; continue with social rhythm only.")
        try:
            from nana.runtime.public_thread_memory import (
                build_public_thread_memory_directive,
                format_public_thread_memory_hint,
            )

            thread_memory = build_public_thread_memory_directive(
                text=text or topic,
                viewer_name=state.display_name,
                recent_turns=recent_turns,
            )
            lines.append(format_public_thread_memory_hint(thread_memory))
        except Exception:
            lines.append("- Public thread memory-lite unavailable; continue without session thread memory.")
        try:
            from nana.runtime.public_running_jokes import (
                build_public_running_joke_directive,
                format_public_running_joke_hint,
            )

            running_joke = build_public_running_joke_directive(
                text=text or topic,
                recent_turns=recent_turns,
            )
            lines.append(format_public_running_joke_hint(running_joke))
        except Exception:
            lines.append("- Public running joke bank unavailable; continue without running joke callbacks.")
        try:
            from nana.runtime.public_viewer_memory import (
                build_public_viewer_memory_directive,
                format_public_viewer_memory_hint,
            )

            viewer_memory = build_public_viewer_memory_directive(
                text=text or topic,
                viewer_name=state.display_name,
                recent_turns=recent_turns,
                actor_key=state.viewer_key if self._bound_scope is not None else None,
            )
            lines.append(format_public_viewer_memory_hint(viewer_memory))
        except Exception:
            lines.append("- Public viewer memory-lite unavailable; continue without per-viewer session memory.")
        try:
            from nana.runtime.public_scene_builder import (
                build_public_scene_directive,
                format_public_scene_hint,
            )

            scene_directive = build_public_scene_directive(
                text=text or topic,
                event_type=event_type,
                room_vibe=room_vibe,
                viewer_name=state.display_name,
                recent_turns=recent_turns,
                repeat_count=repeat_count,
            )
            lines.append(format_public_scene_hint(scene_directive))
        except Exception:
            lines.append("- Public scene builder unavailable; continue without scene/topic card.")
        try:
            from nana.runtime.public_reply_feedback import (
                build_public_reply_feedback_directive,
                format_public_reply_feedback_hint,
            )

            reply_feedback = build_public_reply_feedback_directive(
                text=text or topic,
                last_eval=self._last_public_eval,
            )
            lines.append(format_public_reply_feedback_hint(reply_feedback))
        except Exception:
            lines.append("- Public reply feedback unavailable; continue without eval feedback.")
        try:
            from nana.runtime.core_anchor_recovery import (
                build_core_anchor_directive,
                format_core_anchor_hint,
            )

            core_anchor = build_core_anchor_directive(text or topic)
            lines.append(format_core_anchor_hint(core_anchor))
        except Exception:
            lines.append("- Core anchor recovery unavailable; continue with core self only.")
        try:
            from nana.runtime.public_memory_filter import (
                build_public_memory_filter_directive,
                format_public_memory_filter_hint,
            )

            memory_filter = build_public_memory_filter_directive(
                text=text or topic,
                viewer_name=state.display_name,
                recent_turns=recent_turns,
            )
            lines.append(format_public_memory_filter_hint(memory_filter))
        except Exception:
            lines.append("- Public memory/test filter unavailable; continue without learning-noise metadata.")
        if familiarity == "new_viewer":
            lines.append("- New viewer: warm but a little curious; one greeting is enough, then answer directly.")
        elif familiarity == "returning_session_viewer":
            lines.append("- Returning this session: sound like the conversation is continuing, not restarting.")
        elif familiarity == "familiar_session_viewer":
            lines.append("- Familiar this session: a little more relaxed and playful is okay, but stay public-safe.")
        elif familiarity == "priority_viewer":
            lines.append("- Priority viewer: respond steadily, but still use public persona, not private Ba/con.")
        if room_vibe == "fast_chat":
            lines.append("- Fast chat: shorter reply, no extra follow-up question unless useful.")
        elif room_vibe == "active_chat":
            lines.append("- Active chat: keep momentum with 1-2 compact sentences; avoid long explanations.")
        else:
            lines.append("- Quiet room: 2-3 compact sentences are allowed when the viewer opens a topic; avoid collapsing into one safe line.")
        if event_type == EVENT_STORY_REQUEST:
            lines.append("- Story request: tell a medium public-safe story about stage/chat/viewers, not backend/code/runtime.")
        elif event_type == EVENT_IDENTITY_CHALLENGE:
            lines.append("- Identity challenge: answer with Nana's self-stance and stage boundary; never reduce Nana to a bot, tool, or support assistant.")
        elif event_type == EVENT_QUESTION:
            lines.append("- Question: answer the actual question first, then add one natural public-stage aside if useful.")
        elif event_type == EVENT_GREETING:
            lines.append("- Greeting: greet back naturally, but do not make every future turn a greeting.")
        if director_mode == "mediator":
            lines.append("- Mediator mode: de-escalate gently, do not take sides, then redirect to a lighter public topic.")
        elif director_mode == "repair":
            lines.append("- Repair mode: acknowledge feedback briefly and say Nana will tune the way she talks; do not mention backend, runtime, or processing flow.")
        elif director_mode == "witty":
            lines.append("- Witty mode: a light playful reaction is okay, but do not over-tease.")
        elif director_mode == "storyteller":
            lines.append("- Storyteller mode: tell a compact public-safe story with a beginning, one beat, and an ending; keep it social/stage-flavored, not technical.")
        elif director_mode == "answer":
            lines.append("- Answer mode: answer first; do not dodge with generic filler or a bare invitation.")
        elif director_mode == "greeter":
            lines.append("- Greeter mode: one warm greeting, then invite the actual topic.")
        elif director_mode == "reactor":
            lines.append("- Reactor mode: light reaction only; no long explanation.")
        elif director_mode == "crowd_control":
            lines.append("- Crowd-control mode: keep it short and do not reply to every small message.")
        elif director_mode == "companion":
            lines.append("- Companion mode: continue the shared public thread instead of restarting.")
        return "\n".join(lines)

    def _expire_locked(self, now: float, monotonic_now: float) -> None:
        expired = [
            key
            for key, state in self._viewers.items()
            if monotonic_now - state.last_seen_monotonic > self.session_ttl_seconds
        ]
        for key in expired:
            self._viewers.pop(key, None)
            self._stats["expired_viewers"] += 1
        self._expire_events_locked(monotonic_now)
        self._recent_turns = deque(
            (turn for turn in self._recent_turns
             if monotonic_now - turn.monotonic <= self.session_ttl_seconds),
            maxlen=DEFAULT_RECENT_TURNS_MAX,
        )
        self._topic_stack = deque(
            (turn.topic for turn in self._recent_turns if turn.topic
             and monotonic_now - turn.monotonic <= self.topic_decay_seconds),
            maxlen=DEFAULT_TOPIC_STACK_MAX,
        )
        valid_events = {t.scope.event_id for t in self._recent_turns if t.scope}
        self._delivery = {key: record for key, record in self._delivery.items()
                          if record.event_id in valid_events}
        if not self._recent_turns:
            self._last_public_eval = {"grade": "none", "issue_kinds": []}

    def _expire_events_locked(self, monotonic_now: float) -> None:
        while self._events and monotonic_now - self._events[0][0] > self.velocity_window_seconds:
            self._events.popleft()

    def _chat_velocity_locked(self, monotonic_now: float) -> float:
        self._expire_events_locked(monotonic_now)
        if self.velocity_window_seconds <= 0:
            return 0.0
        return len(self._events) / self.velocity_window_seconds

    def record_reply_context(
        self,
        request=None,
        decision=None,
        reply_text: str = "",
        *,
        now: float | None = None,
        monotonic_now: float | None = None,
        scope: PublicEventScope | None = None,
        text: str | None = None,
        delivery_record: PublicDeliveryRecord | None = None,
    ) -> None:
        """Retain observed viewer input; expose replies only after valid delivery.

        Legacy callers without canonical scope remain isolated from scoped
        production sessions and cannot claim their prepared response delivered.
        """
        current = (self._clock() if self._clock else time.time()) if now is None else float(now)
        current_mono = (self._clock() if self._clock else time.monotonic()) if monotonic_now is None else float(monotonic_now)
        if scope is None:
            scope = getattr(request, "scope", None)
        if scope is not None:
            cache = self._partition(scope)
            if cache is not self:
                return cache.record_reply_context(request, decision, reply_text, now=now,
                    monotonic_now=monotonic_now, scope=scope, text=text, delivery_record=delivery_record)
            with self._lock:
                self._expire_locked(current, current_mono)
                turn = next((t for t in self._recent_turns if t.scope and t.scope.event_id == scope.event_id), None)
                if turn is None:
                    turn = PublicTurn(current, current_mono, scope.display_name, "text", "chill", "", scope=scope)
                    self._recent_turns.append(turn)
                if delivery_record is None:
                    return
                same_scope = (delivery_record.scope.platform, delivery_record.scope.room_id,
                              delivery_record.scope.stream_session_id, delivery_record.scope.event_id,
                              delivery_record.scope.identity.actor_key)
                expected = (scope.platform, scope.room_id, scope.stream_session_id,
                            scope.event_id, scope.identity.actor_key)
                if same_scope != expected:
                    return
                old = self._delivery.get(delivery_record.key)
                if old is not None:
                    if old.delivery_mode != delivery_record.delivery_mode:
                        return
                    receipt = {
                        "event_id": delivery_record.event_id, "output_id": delivery_record.output_id,
                        "attempt_id": delivery_record.attempt_id, "state": delivery_record.state,
                        "revision": delivery_record.revision, "timestamp": delivery_record.updated_at,
                        "platform": scope.platform, "room_id": scope.room_id,
                        "stream_session_id": scope.stream_session_id,
                        "delivery_mode": delivery_record.delivery_mode,
                    }
                    advanced = transition_delivery(old, delivery_record.state, delivery_record.revision,
                                                   delivery_record.attempt_id, receipt)
                    if advanced is old:
                        return
                    delivery_record = advanced
                elif delivery_record.state != "generated":
                    # A caller must register an attempt before forwarding its receipts.
                    return
                self._delivery[delivery_record.key] = delivery_record
                if delivery_record.state == "delivered" and turn.delivery_state != "delivered":
                    replacement = replace(turn, reply_preview=_preview(delivery_record.text_preview, 72),
                                          delivery_state="delivered", attempt_id=delivery_record.attempt_id)
                    self._recent_turns = deque((replacement if t is turn else t for t in self._recent_turns),
                                              maxlen=DEFAULT_RECENT_TURNS_MAX)
            return
        # Compatibility only: unknown legacy replies never become spoken history.
        raw_text = getattr(request, "text", "")
        if not raw_text:
            return
        social_event = classify_social_event(raw_text)
        with self._lock:
            self._expire_locked(current, current_mono)
            self._recent_turns.append(PublicTurn(current, current_mono,
                getattr(request, "author_name", "viewer"), social_event,
                getattr(decision, "director_mode", "chill"), _preview(raw_text, 96),
                action=getattr(decision, "action", ""), topic=infer_topic(raw_text, social_event)))

    def format_public_room_context(self, scope: PublicEventScope | None = None, limit: int = 5) -> str:
        """Build the PUBLIC ROOM CONTEXT block for the public prompt.

        Returns a string block with the last N turns in format:
        - <viewer>: "..."
        - Nana replied: "..."
        Current public topic: ...
        """
        if isinstance(scope, int):
            limit, scope = scope, None
        if scope is not None:
            cache = self._partition(scope)
            if cache is not self:
                return cache.format_public_room_context(scope=scope, limit=limit)
        current = self._clock() if self._clock else time.time()
        mono = self._clock() if self._clock else time.monotonic()
        with self._lock:
            self._expire_locked(current, mono)
            turns = list(self._recent_turns)[-limit:]
            topics = list(self._topic_stack)

        if not turns:
            return ""

        current_actor_key = scope.identity.actor_key if scope is not None else ""
        speaker_refs = {current_actor_key: "current_viewer"} if current_actor_key else {}
        other_viewer_count = 0

        def speaker_label(turn: PublicTurn) -> str:
            nonlocal other_viewer_count
            if not current_actor_key:
                return turn.viewer_name
            actor_key = turn.scope.identity.actor_key if turn.scope is not None else ""
            if not actor_key:
                speaker_ref = "unverified_viewer"
            else:
                speaker_ref = speaker_refs.get(actor_key, "")
                if not speaker_ref:
                    other_viewer_count += 1
                    speaker_ref = f"other_viewer_{other_viewer_count}"
                    speaker_refs[actor_key] = speaker_ref
            # Present this actor consistently under their CURRENT display label;
            # original event labels remain unchanged in the session object.
            display_name = _prompt_display_name(
                scope.display_name if actor_key == current_actor_key else turn.viewer_name)
            return f'[speaker_ref={speaker_ref}; display_name="{display_name}"]'

        turn_lines: list[str] = []
        for turn in turns:
            viewer = speaker_label(turn)
            if turn.reply_preview and turn.delivery_state == "delivered":
                turn_lines.append(f"- {viewer}: \"{turn.message_preview}\"")
                turn_lines.append(f"  Nana replied: \"{turn.reply_preview}\"")
            elif turn.action == ACTION_SKIP:
                turn_lines.append(f"- {viewer}: \"{turn.message_preview}\" [skip/emoji]")
            elif turn.action == ACTION_ACK_ONLY:
                turn_lines.append(f"- {viewer}: \"{turn.message_preview}\"")
                if turn.reply_preview:
                    turn_lines.append(f"  Nana ack: \"{turn.reply_preview}\"")
            elif turn.message_preview:
                turn_lines.append(f"- {viewer}: \"{turn.message_preview}\"")

        if not turn_lines:
            return ""

        lines = ["PUBLIC ROOM CONTEXT:"]
        if current_actor_key:
            lines.extend((
                "- speaker_ref, not display_name, determines ownership of personal facts.",
                "- One speaker_ref may have older display names; equal display names with different speaker_ref values are different viewers.",
                "- Never assign an other_viewer fact to current_viewer. Never mention speaker_ref or account IDs in the reply.",
            ))
        lines.extend(turn_lines)

        if topics:
            current_topic = topics[-1]
            lines.append(f"Current public topic: {current_topic}")
        else:
            lines.append("Current public topic: none")

        return "\n".join(lines)


_SOCIAL_SESSION = SocialSessionCache()


def get_social_session() -> SocialSessionCache:
    return _SOCIAL_SESSION


def social_session_status_lines() -> list[str]:
    snap = get_social_session().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_decision") or {})
    viewer = dict(snap.get("last_viewer") or {})
    lines = [
        "🎚️ Social Session Rhythm",
        f"  Mode: {PHASE} | persistence=session_only | read_only=True | can_act=False",
        (
            "  Cache: "
            f"viewers={snap.get('viewer_count')} | events={snap.get('event_count')} | "
            f"velocity={snap.get('chat_velocity_per_second')}/s over {snap.get('velocity_window_seconds'):.0f}s"
        ),
        (
            "  Thresholds: "
            f"medium={snap.get('medium_velocity_per_second')}/s | "
            f"high={snap.get('high_velocity_per_second')}/s | "
            f"topic_decay={snap.get('topic_decay_seconds'):.0f}s | "
            f"light_ack_cooldown={snap.get('light_reaction_ack_cooldown_seconds'):.0f}s"
        ),
        (
            "  Memory Lite (7E-B): "
            f"recent_turns={snap.get('recent_turns_count')} | "
            f"room_topic={snap.get('room_topic') or 'none'} | "
            f"last_addressed={snap.get('last_addressed_viewer') or 'none'} | "
            f"skipped_light_reactions={snap.get('skipped_light_reactions')}"
        ),
        (
            "  Priority: "
            f"users={', '.join(snap.get('priority_viewers') or []) or 'none'} | "
            "score=5 full-reply lane | persona=public_vtuber_persona"
        ),
        (
            "  Decisions: "
            f"full={stats.get('full_reply', 0)} | ack={stats.get('ack_only', 0)} | "
            f"skip={stats.get('skip', 0)} | emoji/sticker={stats.get('emoji_or_sticker', 0)}"
            f" | light_cooldown={stats.get('light_reaction_cooldown_skip', 0)}"
        ),
    ]
    if last:
        lines.append(
            "  Last decision: "
            f"action={last.get('action')} | reason={last.get('reason')} | "
            f"event={last.get('event_type')} | priority={last.get('priority_score')} | "
            f"vibe={last.get('room_vibe')} | familiarity={last.get('familiarity')} | "
            f"director={last.get('director_mode')}:{last.get('response_shape')} | "
            f"risk={last.get('director_risk')}"
        )
    else:
        lines.append("  Last decision: none")
    if viewer:
        lines.append(
            "  Last viewer: "
            f"{viewer.get('display_name')} | count={viewer.get('message_count')} | "
            f"topic={_preview(viewer.get('last_topic') or 'none', 60)}"
        )
    else:
        lines.append("  Last viewer: none")
    lines.extend(
        [
            "  Rule: emoji/sticker or high-velocity normal chat may skip model calls; priority viewer still gets through.",
            "  Safety: memory_persistence=False | voice_call=False | vts_call=False | obs_call=False | game_input=False",
            "  Live verify: /social-session-status | Discord !nana text/emoji bursts",
        ]
    )
    return lines
