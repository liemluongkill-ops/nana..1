"""STAGE-9B: Intention Planner for Nana.

Nana's short-term intention layer. It reads stream policy, mood continuity, and
public room context, then proposes what Nana should *aim for* next.

This is proposal/state only:
- no LLM call
- no Discord send
- no TTS/VTS/OBS/subtitle
- no game input
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from nana.runtime.mood_continuity import get_mood_continuity
from nana.runtime.stream_state import get_stream_state


PHASE = "STAGE-9B-v2"
INTENTION_TTL_SECONDS = 120.0


class IntentionPriority(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


PRIORITY_RANK = {
    IntentionPriority.HIGH.value: 3,
    IntentionPriority.MEDIUM.value: 2,
    IntentionPriority.LOW.value: 1,
}


@dataclass(frozen=True)
class IntentionCandidate:
    intention: str
    category: str
    priority: str
    tone: str
    active: bool
    reason: str
    prompt_hint: str
    activation_gate: str
    active_since: float = 0.0
    expires_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        now = time.time()
        data["priority_rank"] = PRIORITY_RANK.get(self.priority, 0)
        data["active_age_seconds"] = max(0.0, now - self.active_since) if self.active_since else 0.0
        data["expires_in_seconds"] = max(0.0, self.expires_at - now) if self.expires_at else 0.0
        return data


@dataclass(frozen=True)
class IntentionPlan:
    intention: str
    category: str
    reason: str
    lane: str
    tone: str
    priority: str
    priority_rank: int
    activation_gate: str
    active_since: float = 0.0
    expires_at: float = 0.0
    inactive: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    can_act: bool = False
    can_send: bool = False
    can_call_vts: bool = False
    can_call_tts: bool = False
    prompt_hint: str = ""
    created_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["age_seconds"] = max(0.0, time.time() - float(self.created_at or time.time()))
        data["expires_in_seconds"] = max(0.0, float(self.expires_at or 0.0) - time.time()) if self.expires_at else 0.0
        return data


def _policy_dict(policy: Any) -> dict[str, Any]:
    if policy is None:
        return {}
    if hasattr(policy, "to_dict"):
        return dict(policy.to_dict())
    if isinstance(policy, dict):
        return dict(policy)
    return {}


def _social_snapshot() -> dict[str, Any]:
    try:
        from nana.runtime.social_session import get_social_session

        return get_social_session().snapshot()
    except Exception:
        return {"viewer_count": 0, "chat_velocity_per_second": 0.0, "last_decision": None}


def _social_temperature(mood: dict[str, Any], social: dict[str, Any]) -> dict[str, Any]:
    temp = mood.get("social_temperature")
    if isinstance(temp, dict):
        return dict(temp)
    try:
        from nana.runtime.social_session import get_social_session

        return get_social_session().get_temperature_state()
    except Exception:
        velocity = float(social.get("chat_velocity_per_second") or 0.0)
        return {
            "source": "fallback_social_snapshot",
            "state": "fast" if velocity >= 0.70 else "active" if velocity >= 0.35 else "unknown",
            "confidence": 0.2 if velocity else 0.0,
            "trend": "unknown",
            "chat_velocity_per_second": velocity,
            "chat_velocity_per_minute": round(velocity * 60.0, 2),
            "viewer_count": int(social.get("viewer_count") or 0),
            "event_count": int(social.get("event_count") or 0),
            "viewer_participation": "unknown",
            "engagement_depth": "unknown",
        }


def _candidate(
    *,
    intention: str,
    category: str,
    priority: IntentionPriority,
    tone: str,
    active: bool,
    reason: str,
    prompt_hint: str,
    activation_gate: str,
    active_since: float = 0.0,
) -> IntentionCandidate:
    expires_at = active_since + INTENTION_TTL_SECONDS if active and active_since else 0.0
    return IntentionCandidate(
        intention=intention,
        category=category,
        priority=priority.value,
        tone=tone,
        active=active,
        reason=reason,
        prompt_hint=prompt_hint,
        activation_gate=activation_gate,
        active_since=active_since,
        expires_at=expires_at,
    )


def _candidate_key(candidate: IntentionCandidate) -> str:
    return f"{candidate.category}:{candidate.intention}"


def _build_candidates(
    *,
    policy: dict[str, Any],
    mood: dict[str, Any],
    social: dict[str, Any],
    active_since_by_key: dict[str, float],
    now: float,
) -> list[IntentionCandidate]:
    state = str(policy.get("state") or "offline")
    can_proactive = bool(policy.get("can_proactive"))
    tone = str(policy.get("interaction_tone") or "muted")
    temp = _social_temperature(mood, social)
    temp_state = str(temp.get("state") or "unknown")
    temp_trend = str(temp.get("trend") or "unknown")
    velocity = float(temp.get("chat_velocity_per_second") or 0.0)
    viewer_count = int(temp.get("viewer_count") or social.get("viewer_count") or 0)
    event_count = int(temp.get("event_count") or social.get("event_count") or 0)
    last_event_age = temp.get("last_event_age_seconds")
    try:
        last_event_age_f = float(last_event_age)
    except (TypeError, ValueError):
        last_event_age_f = None

    mood_label = str(mood.get("mood") or "steady")
    focus = float(mood.get("focus") or 0.5)
    tension = float(mood.get("tension") or 0.18)
    playfulness = float(mood.get("playfulness") or 0.48)
    energy = float(mood.get("energy") or 0.5)

    def since(category: str, intention: str, active: bool) -> float:
        key = f"{category}:{intention}"
        if not active:
            return 0.0
        return active_since_by_key.get(key) or now

    live_public = state in {"live_idle", "live_active", "intermission"} and can_proactive
    quiet_or_unknown = temp_state in {"unknown", "dormant", "cooling"}
    no_recent_viewer = last_event_age_f is None or last_event_age_f >= 30.0 or event_count == 0

    candidates = [
        _candidate(
            intention="standby_observe",
            category="observer",
            priority=IntentionPriority.LOW,
            tone="muted" if not live_public else "quiet",
            active=not live_public,
            reason=f"stream_state={state}, proactive={can_proactive}",
            activation_gate="stream policy blocks proactive public speech",
            prompt_hint="Do not start public topics while stream policy blocks proactive speech.",
            active_since=since("observer", "standby_observe", not live_public),
        ),
        _candidate(
            intention="recover_composure",
            category="observer",
            priority=IntentionPriority.HIGH,
            tone="focused",
            active=live_public and tension >= 0.55 and focus >= 0.58,
            reason=f"mood={mood_label}, tension={tension:.2f}, focus={focus:.2f}",
            activation_gate="internal tension/focus high while public stream can speak",
            prompt_hint="Sound steady and grounded; avoid over-joking until Nana stabilizes.",
            active_since=since("observer", "recover_composure", live_public and tension >= 0.55 and focus >= 0.58),
        ),
        _candidate(
            intention="host_room_momentum",
            category="stream_host",
            priority=IntentionPriority.HIGH,
            tone="snappy" if temp_state == "fast" else "gentle",
            active=live_public and temp_state in {"cooling", "fast"},
            reason=f"social_temperature={temp_state}, trend={temp_trend}, velocity={velocity:.2f}/s",
            activation_gate="social temperature cooling/fast from social_session",
            prompt_hint="Bias content toward hosting the room's rhythm; do not monologue or greet every viewer.",
            active_since=since("stream_host", "host_room_momentum", live_public and temp_state in {"cooling", "fast"}),
        ),
        _candidate(
            intention="social_warmup_seed",
            category="social_warmup",
            priority=IntentionPriority.MEDIUM,
            tone="playful" if playfulness >= 0.56 else "gentle",
            active=live_public and quiet_or_unknown and no_recent_viewer and viewer_count <= 2,
            reason=f"social_temperature={temp_state}, last_event_age={last_event_age_f}, viewers={viewer_count}",
            activation_gate="cooling/unknown social temperature with little recent viewer activity",
            prompt_hint="Bias toward one small public-safe topic seed from Nana's world; keep it short.",
            active_since=since(
                "social_warmup",
                "social_warmup_seed",
                live_public and quiet_or_unknown and no_recent_viewer and viewer_count <= 2,
            ),
        ),
        _candidate(
            intention="story_seed",
            category="story_teller",
            priority=IntentionPriority.MEDIUM,
            tone="warm",
            active=live_public and temp_state == "dormant" and viewer_count > 3,
            reason=f"social_temperature=dormant, viewers={viewer_count}",
            activation_gate="dormant room with enough viewers to reward a compact story",
            prompt_hint="Bias toward a compact story beat; do not reveal private memories or backend details.",
            active_since=since("story_teller", "story_seed", live_public and temp_state == "dormant" and viewer_count > 3),
        ),
        _candidate(
            intention="game_focus_commentary",
            category="game_focus",
            priority=IntentionPriority.MEDIUM,
            tone="focused",
            active=False,
            reason="game state signal unavailable in this smoke-only layer",
            activation_gate="requires explicit game-state signal; never infer from chat text",
            prompt_hint="Reserved for future game-aware commentary; no game input is allowed here.",
            active_since=0.0,
        ),
        _candidate(
            intention="keep_stage_alive",
            category="stream_host",
            priority=IntentionPriority.LOW,
            tone=tone if tone != "muted" else "gentle",
            active=live_public,
            reason=f"fallback live public bias, social_temperature={temp_state}",
            activation_gate="live public stream policy permits proactive content bias",
            prompt_hint="Maintain Nana's public stage presence as the main character, not an assistant.",
            active_since=since("stream_host", "keep_stage_alive", live_public),
        ),
    ]
    return candidates


def _resolve_candidate(candidates: list[IntentionCandidate], now: float) -> tuple[IntentionCandidate, list[IntentionCandidate], list[IntentionCandidate]]:
    active: list[IntentionCandidate] = []
    inactive: list[IntentionCandidate] = []
    for candidate in candidates:
        if not candidate.active:
            inactive.append(candidate)
            continue
        if candidate.expires_at and now >= candidate.expires_at:
            inactive.append(
                IntentionCandidate(
                    intention=candidate.intention,
                    category=candidate.category,
                    priority=candidate.priority,
                    tone=candidate.tone,
                    active=False,
                    reason=f"expired_after_{INTENTION_TTL_SECONDS:.0f}s",
                    prompt_hint=candidate.prompt_hint,
                    activation_gate=candidate.activation_gate,
                    active_since=candidate.active_since,
                    expires_at=candidate.expires_at,
                )
            )
            continue
        active.append(candidate)

    if not active:
        fallback = next((item for item in candidates if item.intention == "standby_observe"), candidates[0])
        return fallback, inactive, []

    active.sort(
        key=lambda item: (
            PRIORITY_RANK.get(item.priority, 0),
            now - item.active_since if item.active_since else 0.0,
        ),
        reverse=True,
    )
    selected = active[0]
    conflicts = active[1:]
    inactive.extend(conflicts)
    return selected, inactive, conflicts


class IntentionPlanner:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._stats = {"planned": 0, "preview": 0, "blocked": 0}
        self._active_since_by_key: dict[str, float] = {}
        self._last_candidates: list[dict[str, Any]] = []
        self._last_plan = IntentionPlan(
            intention="standby_observe",
            category="observer",
            reason="init",
            lane="public_stage",
            tone="muted",
            priority=IntentionPriority.LOW.value,
            priority_rank=PRIORITY_RANK[IntentionPriority.LOW.value],
            activation_gate="init",
            prompt_hint="No intention planned yet.",
            created_at=self._initialized_at,
        )

    @classmethod
    def reset_for_test(cls) -> None:
        global _PLANNER
        _PLANNER = IntentionPlanner()

    def plan(self, *, dry_run: bool = True) -> IntentionPlan:
        now = time.time()
        policy = _policy_dict(get_stream_state().get_policy())
        mood = get_mood_continuity().snapshot().to_dict()
        social = _social_snapshot()
        with self._lock:
            candidates = _build_candidates(
                policy=policy,
                mood=mood,
                social=social,
                active_since_by_key=dict(self._active_since_by_key),
                now=now,
            )
            for candidate in candidates:
                key = _candidate_key(candidate)
                if candidate.active and not candidate.active_since:
                    self._active_since_by_key[key] = now
                elif not candidate.active:
                    self._active_since_by_key.pop(key, None)
            candidates = _build_candidates(
                policy=policy,
                mood=mood,
                social=social,
                active_since_by_key=dict(self._active_since_by_key),
                now=now,
            )
            selected, inactive, conflicts = _resolve_candidate(candidates, now)
            active_keys = {_candidate_key(candidate) for candidate in candidates if candidate.active}
            for key in list(self._active_since_by_key):
                if key not in active_keys:
                    self._active_since_by_key.pop(key, None)
            candidate_dicts = [candidate.to_dict() for candidate in candidates]
            inactive_dicts = tuple(candidate.to_dict() for candidate in inactive if candidate.intention != selected.intention)
            conflict_dicts = tuple(candidate.to_dict() for candidate in conflicts)
        plan = IntentionPlan(
            intention=selected.intention,
            category=selected.category,
            reason=selected.reason,
            lane="public_stage",
            tone=selected.tone,
            priority=selected.priority,
            priority_rank=PRIORITY_RANK.get(selected.priority, 0),
            activation_gate=selected.activation_gate,
            active_since=selected.active_since,
            expires_at=selected.expires_at,
            inactive=inactive_dicts,
            conflicts=conflict_dicts,
            prompt_hint=selected.prompt_hint,
            created_at=now,
        )
        with self._lock:
            self._stats["preview" if dry_run else "planned"] += 1
            self._last_candidates = candidate_dicts
            if not dry_run:
                self._last_plan = plan
        return plan

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "phase": PHASE,
                "initialized_age_seconds": max(0.0, time.time() - self._initialized_at),
                "stats": dict(self._stats),
                "last_plan": self._last_plan.to_dict(),
                "candidates": list(self._last_candidates),
                "safety": {
                    "can_act": False,
                    "can_send": False,
                    "tts_call": False,
                    "vts_call": False,
                    "obs_call": False,
                    "game_input": False,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = dict(snap.get("stats") or {})
        last = dict(snap.get("last_plan") or {})
        candidates = list(snap.get("candidates") or [])
        inactive = [item for item in candidates if not item.get("active")]
        return [
            "🧭 Intention Planner",
            f"  Phase: {PHASE} | read_only=True | can_act=False",
            (
                "  Active: "
                f"{last.get('category')}:{last.get('intention')} | priority={last.get('priority')} "
                f"(rank={last.get('priority_rank')}) | tone={last.get('tone')}"
            ),
            f"  Gate: {last.get('activation_gate')} | reason={last.get('reason')}",
            f"  Expires in: {last.get('expires_in_seconds', 0):.0f}s | conflicts={len(last.get('conflicts') or [])}",
            f"  Hint: {last.get('prompt_hint')}",
            (
                "  Inactive: "
                + (
                    " | ".join(
                        f"{item.get('category')}:{item.get('intention')}({item.get('reason')})"
                        for item in inactive[:4]
                    )
                    if inactive
                    else "none"
                )
            ),
            (
                "  Stats: "
                f"planned={stats.get('planned', 0)} | preview={stats.get('preview', 0)} | "
                f"blocked={stats.get('blocked', 0)}"
            ),
            "  Commands: /intention-status | /intention-preview | /intention-refresh",
            "  Safety: proposal-only; no LLM/TTS/VTS/OBS/Discord/game input",
        ]

    def preview_lines(self, *, dry_run: bool = True) -> list[str]:
        plan = self.plan(dry_run=dry_run)
        title = "Intention Preview" if dry_run else "Intention Refresh"
        return [
            f"🧭 {title}",
            f"  Phase: {PHASE} | read_only=True | can_act=False",
            f"  Active: {plan.category}:{plan.intention}",
            f"  Tone: {plan.tone} | priority={plan.priority} | rank={plan.priority_rank}",
            f"  Gate: {plan.activation_gate}",
            f"  Reason: {plan.reason}",
            f"  Conflicts: {len(plan.conflicts)} | inactive={len(plan.inactive)} | expires_in={plan.to_dict().get('expires_in_seconds', 0):.0f}s",
            f"  Hint: {plan.prompt_hint}",
            f"  Stored: {not dry_run}",
            "  Safety: proposal-only; no chat send and no output action.",
        ]


_PLANNER = IntentionPlanner()


def get_intention_planner() -> IntentionPlanner:
    return _PLANNER


def intention_status_lines() -> list[str]:
    return get_intention_planner().status_lines()


def intention_preview_lines() -> list[str]:
    return get_intention_planner().preview_lines(dry_run=True)


def intention_refresh_lines() -> list[str]:
    return get_intention_planner().preview_lines(dry_run=False)
