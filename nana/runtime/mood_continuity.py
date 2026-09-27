"""STAGE-9A: Mood Continuity Core for Nana.

This module keeps a slow-moving session mood separate from per-message emotion.
It is state/prompt context only: no LLM judge, no output action, no TTS/VTS/OBS,
no Discord send, and no game input.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

from nana.memory import memory, memory_lock, save_memory_async


PHASE = "STAGE-9A"
PHASE_V2 = "STAGE-9A-v2"
MEMORY_KEY = "mood_continuity"

BASELINE = {
    "energy": 0.50,
    "warmth": 0.55,
    "playfulness": 0.48,
    "focus": 0.50,
    "tension": 0.18,
}

DECAY_WINDOW_SECONDS = 20 * 60
PERSIST_THROTTLE_SECONDS = 60


POSITIVE_RE = re.compile(r"(haha|hehe|=+\)|cute|dễ\s*thương|vui|ngon|hay|thích|tuyệt|ổn|ok|nice)", re.IGNORECASE)
TECH_RE = re.compile(r"(bug|lỗi|error|traceback|exception|debug|code|fix|runtime|token|bridge|vts|obs|api)", re.IGNORECASE)
QUIET_RE = re.compile(r"(im|yên|trầm|vắng|buồn\s*ngủ|mệt|khuya|lặng)", re.IGNORECASE)
FRICTION_RE = re.compile(r"(không\s*ổn|loạn|câm|đơ|fail|hỏng|chết|sai|lệch)", re.IGNORECASE)
PUBLIC_RE = re.compile(r"(stream|live|chat|viewer|người\s*xem|khán\s*giả|discord|mọi\s*người)", re.IGNORECASE)


def _clamp(value: Any, low: float = 0.0, high: float = 1.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    return max(low, min(high, number))


def _now() -> float:
    return time.time()


def _default_state(now: float | None = None) -> dict[str, Any]:
    current = _now() if now is None else float(now)
    return {
        "phase": PHASE,
        "energy": BASELINE["energy"],
        "warmth": BASELINE["warmth"],
        "playfulness": BASELINE["playfulness"],
        "focus": BASELINE["focus"],
        "tension": BASELINE["tension"],
        "mood": "steady",
        "anchor": "session_start",
        "last_reason": "init",
        "last_source": "init",
        "last_lane": "unknown",
        "last_update": current,
        "last_decay": current,
        "session_events": 0,
        "persisted_events": 0,
    }


def _merge_state(raw: dict[str, Any] | None) -> dict[str, Any]:
    state = _default_state()
    if isinstance(raw, dict):
        state.update(raw)
    for key in ("energy", "warmth", "playfulness", "focus", "tension"):
        state[key] = _clamp(state.get(key), 0.0, 1.0)
    for key in ("last_update", "last_decay"):
        try:
            state[key] = float(state.get(key) or _now())
        except (TypeError, ValueError):
            state[key] = _now()
    for key in ("session_events", "persisted_events"):
        try:
            state[key] = int(state.get(key) or 0)
        except (TypeError, ValueError):
            state[key] = 0
    state["phase"] = PHASE
    state["mood"] = _derive_mood(state)
    return state


def _lane_key(lane: str | None) -> str:
    raw = str(lane or "").strip()
    aliases = {
        "private": "private_owner",
        "private_owner": "private_owner",
        "public": "public_stage",
        "public_viewer": "public_stage",
        "public_stage": "public_stage",
        "operator": "operator_backstage",
        "operator_backstage": "operator_backstage",
        "bridge_system": "operator_backstage",
    }
    return aliases.get(raw, "unknown")


def _derive_mood(state: dict[str, Any]) -> str:
    energy = _clamp(state.get("energy"))
    warmth = _clamp(state.get("warmth"))
    play = _clamp(state.get("playfulness"))
    focus = _clamp(state.get("focus"))
    tension = _clamp(state.get("tension"))
    if tension >= 0.62 and focus >= 0.56:
        return "strained_focus"
    if focus >= 0.68 and tension < 0.55:
        return "focused"
    if energy <= 0.34:
        return "quiet_tired"
    if play >= 0.66 and energy >= 0.48:
        return "bright_playful"
    if warmth >= 0.68 and tension < 0.45:
        return "warm"
    if energy >= 0.62 and focus >= 0.52:
        return "awake"
    return "steady"


def _event_delta(text: str, event_type: str) -> tuple[dict[str, float], str]:
    raw = str(text or "")
    event = str(event_type or "message").strip().lower()
    delta = {
        "energy": 0.0,
        "warmth": 0.0,
        "playfulness": 0.0,
        "focus": 0.0,
        "tension": 0.0,
    }
    reasons: list[str] = []

    if event in {"stream_start", "live_on"}:
        delta.update({"energy": 0.06, "focus": 0.04})
        reasons.append("stream_start")
    elif event in {"stream_end", "post_stream"}:
        delta.update({"energy": -0.08, "focus": -0.04, "warmth": 0.02})
        reasons.append("stream_end")
    elif event in {"auto_starter", "starter"}:
        delta.update({"energy": 0.03, "playfulness": 0.04, "warmth": 0.02})
        reasons.append("starter")
    elif event in {"emoji_only", "sticker"}:
        delta.update({"energy": 0.04, "playfulness": 0.08, "warmth": 0.03})
        reasons.append("light_reaction")

    if POSITIVE_RE.search(raw):
        delta["warmth"] += 0.05
        delta["playfulness"] += 0.06
        delta["energy"] += 0.02
        reasons.append("positive_chat")
    if TECH_RE.search(raw):
        delta["focus"] += 0.08
        delta["tension"] += 0.04
        delta["playfulness"] -= 0.01
        reasons.append("technical_focus")
    if QUIET_RE.search(raw):
        delta["energy"] -= 0.05
        delta["warmth"] += 0.02
        reasons.append("quiet_room")
    if FRICTION_RE.search(raw):
        delta["tension"] += 0.10
        delta["focus"] += 0.05
        delta["energy"] -= 0.03
        reasons.append("friction")
    if PUBLIC_RE.search(raw):
        delta["focus"] += 0.02
        delta["energy"] += 0.01
        reasons.append("stage_context")

    if not reasons:
        reasons.append("small_continuity")
        delta["warmth"] += 0.01
    return delta, "+".join(reasons)


@dataclass(frozen=True)
class MoodSnapshot:
    phase: str
    mood: str
    energy: float
    warmth: float
    playfulness: float
    focus: float
    tension: float
    anchor: str
    last_reason: str
    last_source: str
    last_lane: str
    age_seconds: float
    session_events: int
    persisted_events: int
    internal_mood: dict[str, Any] | None = None
    social_temperature: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        internal = self.internal_mood or {
            "mood": self.mood,
            "energy": self.energy,
            "warmth": self.warmth,
            "playfulness": self.playfulness,
            "focus": self.focus,
            "tension": self.tension,
            "anchor": self.anchor,
            "last_reason": self.last_reason,
            "last_source": self.last_source,
            "last_lane": self.last_lane,
            "age_seconds": self.age_seconds,
        }
        social = self.social_temperature or _social_temperature_snapshot()
        data = {
            "phase": self.phase,
            "mood": self.mood,
            "energy": self.energy,
            "warmth": self.warmth,
            "playfulness": self.playfulness,
            "focus": self.focus,
            "tension": self.tension,
            "anchor": self.anchor,
            "last_reason": self.last_reason,
            "last_source": self.last_source,
            "last_lane": self.last_lane,
            "age_seconds": self.age_seconds,
            "session_events": self.session_events,
            "persisted_events": self.persisted_events,
            "internal_mood": dict(internal),
            "social_temperature": dict(social),
        }
        return data


def _social_temperature_snapshot() -> dict[str, Any]:
    """Reference social room temperature from social_session, never infer it here."""
    try:
        from nana.runtime.social_session import get_social_session

        temp = get_social_session().get_temperature_state()
        if isinstance(temp, dict):
            return temp
    except Exception as exc:
        return {
            "source": "social_session_unavailable",
            "state": "unknown",
            "confidence": 0.0,
            "trend": "unknown",
            "chat_velocity_per_second": 0.0,
            "chat_velocity_per_minute": 0.0,
            "viewer_count": 0,
            "event_count": 0,
            "viewer_participation": "unknown",
            "engagement_depth": "unknown",
            "reason": f"{type(exc).__name__}",
            "read_only": True,
            "can_act": False,
        }
    return {
        "source": "social_session",
        "state": "unknown",
        "confidence": 0.0,
        "trend": "unknown",
        "chat_velocity_per_second": 0.0,
        "chat_velocity_per_minute": 0.0,
        "viewer_count": 0,
        "event_count": 0,
        "viewer_participation": "unknown",
        "engagement_depth": "unknown",
        "read_only": True,
        "can_act": False,
    }


class MoodContinuity:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._last_persist_at = 0.0
        with memory_lock:
            self._state = _merge_state(memory.get(MEMORY_KEY))
            memory[MEMORY_KEY] = dict(self._state)

    @classmethod
    def reset_for_test(cls) -> None:
        global _MOOD
        _MOOD = MoodContinuity()
        _MOOD.reset(persist=False)

    def _decay_locked(self, now: float) -> None:
        last_decay = float(self._state.get("last_decay") or now)
        elapsed = max(0.0, now - last_decay)
        if elapsed < 1.0:
            return
        ratio = min(1.0, elapsed / DECAY_WINDOW_SECONDS)
        for key, baseline in BASELINE.items():
            current = _clamp(self._state.get(key))
            self._state[key] = _clamp(current + (baseline - current) * ratio)
        self._state["last_decay"] = now
        self._state["mood"] = _derive_mood(self._state)

    def snapshot(self) -> MoodSnapshot:
        now = _now()
        with self._lock:
            self._decay_locked(now)
            state = dict(self._state)
        internal = {
            "mood": str(state.get("mood") or "steady"),
            "energy": _clamp(state.get("energy")),
            "warmth": _clamp(state.get("warmth")),
            "playfulness": _clamp(state.get("playfulness")),
            "focus": _clamp(state.get("focus")),
            "tension": _clamp(state.get("tension")),
            "anchor": str(state.get("anchor") or "none"),
            "last_reason": str(state.get("last_reason") or "none"),
            "last_source": str(state.get("last_source") or "none"),
            "last_lane": str(state.get("last_lane") or "unknown"),
            "age_seconds": max(0.0, now - float(state.get("last_update") or now)),
        }
        return MoodSnapshot(
            phase=PHASE,
            mood=internal["mood"],
            energy=internal["energy"],
            warmth=internal["warmth"],
            playfulness=internal["playfulness"],
            focus=internal["focus"],
            tension=internal["tension"],
            anchor=internal["anchor"],
            last_reason=internal["last_reason"],
            last_source=internal["last_source"],
            last_lane=internal["last_lane"],
            age_seconds=internal["age_seconds"],
            session_events=int(state.get("session_events") or 0),
            persisted_events=int(state.get("persisted_events") or 0),
            internal_mood=internal,
            social_temperature=_social_temperature_snapshot(),
        )

    def observe_text(
        self,
        text: str,
        *,
        lane: str | None = None,
        source: str = "chat",
        event_type: str = "message",
        persist: bool = True,
    ) -> MoodSnapshot:
        now = _now()
        lane_norm = _lane_key(lane)
        delta, reason = _event_delta(text, event_type)
        should_persist = False
        with self._lock:
            self._decay_locked(now)
            for key, amount in delta.items():
                self._state[key] = _clamp(self._state.get(key, BASELINE[key]) + amount)
            self._state["mood"] = _derive_mood(self._state)
            self._state["anchor"] = str(text or event_type or "event").strip()[:120] or "event"
            self._state["last_reason"] = reason
            self._state["last_source"] = str(source or "chat")
            self._state["last_lane"] = lane_norm
            self._state["last_update"] = now
            self._state["session_events"] = int(self._state.get("session_events") or 0) + 1
            state_copy = dict(self._state)
            if persist and now - self._last_persist_at >= PERSIST_THROTTLE_SECONDS:
                self._last_persist_at = now
                self._state["persisted_events"] = int(self._state.get("persisted_events") or 0) + 1
                state_copy = dict(self._state)
                should_persist = True
        with memory_lock:
            memory[MEMORY_KEY] = dict(state_copy)
        if should_persist:
            save_memory_async()
        return self.snapshot()

    def reset(self, *, persist: bool = True) -> MoodSnapshot:
        now = _now()
        with self._lock:
            self._state = _default_state(now)
            self._state["last_reason"] = "manual_reset"
            state_copy = dict(self._state)
            self._last_persist_at = now if persist else 0.0
        with memory_lock:
            memory[MEMORY_KEY] = dict(state_copy)
        if persist:
            save_memory_async()
        return self.snapshot()

    def prompt_block(self, lane: str | None = None) -> str:
        lane_norm = _lane_key(lane)
        snap = self.snapshot()
        social = dict(snap.social_temperature or {})
        if lane_norm == "public_stage":
            lane_rule = "Public stage: treat this as background stage mood; do not reveal private relationship or private memories."
            if snap.last_lane != "public_stage":
                anchor = "private_or_backstage_context_withheld"
                reason = "background_mood_continuity"
                source = "withheld"
                last_lane = "withheld"
            else:
                anchor = snap.anchor
                reason = snap.last_reason
                source = snap.last_source
                last_lane = snap.last_lane
        elif lane_norm == "operator_backstage":
            lane_rule = "Operator backstage: keep status clear and low-drama."
            anchor = snap.anchor
            reason = snap.last_reason
            source = snap.last_source
            last_lane = snap.last_lane
        else:
            lane_rule = "Private owner lane: familiar warmth is allowed, but do not invent memories."
            anchor = snap.anchor
            reason = snap.last_reason
            source = snap.last_source
            last_lane = snap.last_lane
        return (
            "MOOD CONTINUITY (INTERNAL, STAGE-9A-v2)\n"
            f"- mood={snap.mood}\n"
            f"- energy={snap.energy:.2f} warmth={snap.warmth:.2f} playfulness={snap.playfulness:.2f} "
            f"focus={snap.focus:.2f} tension={snap.tension:.2f}\n"
            f"- recent_anchor={anchor}\n"
            f"- reason={reason} source={source} lane={last_lane}\n"
            "- Guidance: keep emotional continuity across turns; adjust tone gradually instead of resetting every message.\n"
            "- Do not mention these internal numbers unless operator explicitly asks.\n"
            f"- {lane_rule}\n"
            "SOCIAL TEMPERATURE (DERIVED FROM social_session)\n"
            f"- state={social.get('state', 'unknown')} confidence={social.get('confidence', 0.0)} trend={social.get('trend', 'unknown')}\n"
            f"- velocity={social.get('chat_velocity_per_minute', 0.0)}msg/min viewers={social.get('viewer_count', 0)} events={social.get('event_count', 0)}\n"
            f"- participation={social.get('viewer_participation', 'unknown')} engagement_depth={social.get('engagement_depth', 'unknown')}\n"
            "- Use social temperature only for action gates/rhythm; do not borrow internal warmth as room engagement."
        )

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        social = dict(snap.social_temperature or {})
        return [
            "🌗 Mood Continuity Core",
            f"  Phase: {PHASE_V2} | read_only=True | can_act=False",
            (
                "  Internal mood: "
                f"{snap.mood} | energy={snap.energy:.2f} | warmth={snap.warmth:.2f} | "
                f"playfulness={snap.playfulness:.2f} | focus={snap.focus:.2f} | tension={snap.tension:.2f}"
            ),
            (
                "  Social temperature: "
                f"state={social.get('state', 'unknown')} | trend={social.get('trend', 'unknown')} | "
                f"velocity={social.get('chat_velocity_per_minute', 0.0)}msg/min | "
                f"viewers={social.get('viewer_count', 0)} | engagement={social.get('engagement_depth', 'unknown')}"
            ),
            (
                "  Last: "
                f"source={snap.last_source} | lane={snap.last_lane} | reason={snap.last_reason} | "
                f"age={snap.age_seconds:.1f}s"
            ),
            f"  Anchor: {snap.anchor}",
            f"  Events: session={snap.session_events} | persisted={snap.persisted_events}",
            "  Prompt: internal mood affects tone; social temperature affects action/rhythm gates.",
            "  Safety: no LLM judge | no live action | no TTS/VTS/OBS/Discord/game input",
            "  Commands: /mood-status | /mood-preview [lane] | /mood-test <text> | /mood-reset",
        ]

    def preview_lines(self, lane: str | None = None) -> list[str]:
        return [
            "🌗 Mood Prompt Preview",
            f"  Phase: {PHASE_V2} | read_only=True | can_act=False",
            "  Block:",
            *[f"    {line}" for line in self.prompt_block(lane).splitlines()],
            "  Safety: prompt preview only; no LLM call and no output action.",
        ]

    def test_lines(self, text: str, lane: str | None = None) -> list[str]:
        sample = str(text or "").strip()
        snap = self.observe_text(sample, lane=lane or "private_owner", source="manual-test", event_type="message", persist=False)
        return [
            "🧪 Mood Continuity Test",
            f"  Input: {sample or '(empty)'}",
            f"  Mood: {snap.mood} | energy={snap.energy:.2f} | warmth={snap.warmth:.2f} | playfulness={snap.playfulness:.2f} | focus={snap.focus:.2f} | tension={snap.tension:.2f}",
            f"  Reason: {snap.last_reason}",
            "  Safety: local state only; no LLM/TTS/VTS/OBS/Discord/game input.",
        ]


_MOOD = MoodContinuity()


def get_mood_continuity() -> MoodContinuity:
    return _MOOD


def observe_mood_text(
    text: str,
    *,
    lane: str | None = None,
    source: str = "chat",
    event_type: str = "message",
    persist: bool = True,
) -> MoodSnapshot:
    return get_mood_continuity().observe_text(
        text,
        lane=lane,
        source=source,
        event_type=event_type,
        persist=persist,
    )


def format_mood_prompt_block(lane: str | None = None) -> str:
    return get_mood_continuity().prompt_block(lane)


def mood_status_lines() -> list[str]:
    return get_mood_continuity().status_lines()


def mood_preview_lines(lane: str | None = None) -> list[str]:
    return get_mood_continuity().preview_lines(lane)


def mood_test_lines(text: str, lane: str | None = None) -> list[str]:
    return get_mood_continuity().test_lines(text, lane)


def mood_reset_lines() -> list[str]:
    snap = get_mood_continuity().reset()
    return [
        "🌗 Mood Continuity Reset",
        f"  Mood: {snap.mood} | energy={snap.energy:.2f} | warmth={snap.warmth:.2f} | playfulness={snap.playfulness:.2f}",
        "  Safety: state reset only; no live action.",
    ]
