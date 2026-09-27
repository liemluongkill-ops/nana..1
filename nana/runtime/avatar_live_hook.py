"""STAGE-8L: live public event -> avatar reaction hook.

This module connects STAGE-8K avatar event metadata to STAGE-8H reaction plans.
It is still a controlled hook: it can build/record reaction plans, but it never
dispatches VTS/OBS/TTS/Discord/game actions. Real VTS dispatch remains owned by
STAGE-8I's explicit-enable gate.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass, replace
from typing import Any, Optional

from nana.runtime.avatar_event_bridge import AvatarEventRecord, get_avatar_event_bridge
from nana.runtime.avatar_reactor import AvatarReactionPlan, get_avatar_reaction_controller


PHASE = "STAGE-8L"
DEFAULT_MIN_EVENT_AGE_SECONDS = 15.0


@dataclass(frozen=True)
class AvatarLiveHookResult:
    ok: bool
    action: str
    reason: str
    event: str = "message"
    source: str = ""
    viewer_name: str = ""
    expression: str = "neutral"
    hotkey: str = ""
    body_language: str = "minimal"
    duration_s: float = 0.0
    dry_run: bool = True
    vts_call: bool = False
    event_age_seconds: float = 0.0
    runtime_action: str = ""
    runtime_status: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class AvatarLiveHook:
    """Session-only hook from latest public event metadata to reaction plan."""

    _instance: Optional["AvatarLiveHook"] = None
    _instance_lock = threading.Lock()

    def __init__(self, *, max_event_age_seconds: float = DEFAULT_MIN_EVENT_AGE_SECONDS) -> None:
        self.max_event_age_seconds = float(max_event_age_seconds)
        self._lock = threading.RLock()
        self._last_result = AvatarLiveHookResult(False, "none", "none")
        self._last_event_key = ""
        self._stats = {
            "preview": 0,
            "planned": 0,
            "blocked": 0,
            "stale": 0,
            "duplicate": 0,
            "policy": 0,
        }

    @classmethod
    def get_instance(cls) -> "AvatarLiveHook":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def _event_key(self, record: AvatarEventRecord) -> str:
        return "|".join(
            [
                str(record.event or ""),
                str(record.source or ""),
                str(record.viewer_name or ""),
                str(record.request_id or ""),
                f"{record.created_at:.6f}",
            ]
        )

    def _record(self, result: AvatarLiveHookResult, *, stat: str = "") -> AvatarLiveHookResult:
        with self._lock:
            if result.ok:
                if result.dry_run:
                    self._stats["preview"] += 1
                else:
                    self._stats["planned"] += 1
            else:
                self._stats["blocked"] += 1
                if stat in self._stats:
                    self._stats[stat] += 1
            self._last_result = result
        return result

    def _from_plan(
        self,
        record: AvatarEventRecord,
        plan: AvatarReactionPlan,
        *,
        dry_run: bool,
        age_seconds: float,
    ) -> AvatarLiveHookResult:
        if not plan.ok:
            return AvatarLiveHookResult(
                False,
                "blocked",
                f"reaction_plan_blocked:{plan.reason}",
                event=record.event,
                source=record.source,
                viewer_name=record.viewer_name,
                expression=plan.expression,
                hotkey=plan.hotkey,
                body_language=plan.body_language,
                duration_s=plan.duration_s,
                dry_run=dry_run,
                vts_call=False,
                event_age_seconds=age_seconds,
            )
        return AvatarLiveHookResult(
            True,
            "would_plan" if dry_run else "planned",
            "preview_only" if dry_run else "reaction_plan_recorded_no_vts",
            event=record.event,
            source=record.source,
            viewer_name=record.viewer_name,
            expression=plan.expression,
            hotkey=plan.hotkey,
            body_language=plan.body_language,
            duration_s=plan.duration_s,
            dry_run=dry_run,
            vts_call=False,
            event_age_seconds=age_seconds,
        )

    def evaluate_latest(
        self,
        *,
        dry_run: bool = True,
        allow_stale: bool = False,
        allow_duplicate: bool = False,
    ) -> AvatarLiveHookResult:
        record = get_avatar_event_bridge().last_event()
        now = time.time()
        age = max(0.0, now - record.created_at)
        if record.source == "init":
            return self._record(
                AvatarLiveHookResult(
                    False,
                    "blocked",
                    "no_public_event",
                    event=record.event,
                    source=record.source,
                    dry_run=dry_run,
                    event_age_seconds=age,
                ),
                stat="policy",
            )
        if not allow_stale and age > self.max_event_age_seconds:
            return self._record(
                AvatarLiveHookResult(
                    False,
                    "blocked",
                    f"stale_event:{age:.1f}s",
                    event=record.event,
                    source=record.source,
                    viewer_name=record.viewer_name,
                    dry_run=dry_run,
                    event_age_seconds=age,
                ),
                stat="stale",
            )

        event_key = self._event_key(record)
        with self._lock:
            duplicate = bool(event_key and event_key == self._last_event_key)
        if not dry_run and duplicate and not allow_duplicate:
            return self._record(
                AvatarLiveHookResult(
                    False,
                    "blocked",
                    "duplicate_event",
                    event=record.event,
                    source=record.source,
                    viewer_name=record.viewer_name,
                    dry_run=dry_run,
                    event_age_seconds=age,
                ),
                stat="duplicate",
            )

        plan = get_avatar_reaction_controller().build_plan(
            chat_event=record.event,
            dry_run=dry_run,
        )
        result = self._from_plan(record, plan, dry_run=dry_run, age_seconds=age)
        if result.ok and not dry_run:
            result = self._publish_runtime_intent(record, result)
            with self._lock:
                self._last_event_key = event_key
        return self._record(result, stat="policy" if not result.ok else "")

    def _publish_runtime_intent(
        self,
        record: AvatarEventRecord,
        result: AvatarLiveHookResult,
    ) -> AvatarLiveHookResult:
        """Optionally hand a sparse public event to the semantic gateway.

        The feature is opt-in in the gateway. A missing or disabled runtime
        must never block the existing metadata/reaction planning path.
        """
        try:
            from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway

            runtime_result = get_avatar_intent_gateway().submit_event(
                record.event,
                source=record.source or "public_event",
                channel="public",
                correlation_id=record.request_id,
                metadata={"viewer": record.viewer_name, "channel": record.channel},
            )
            receipt = runtime_result.receipt
            return replace(
                result,
                runtime_action=(runtime_result.intent.action if runtime_result.intent else ""),
                runtime_status=(
                    receipt.status
                    if receipt is not None
                    else runtime_result.reason
                ),
            )
        except Exception as exc:
            return replace(
                result,
                runtime_status=f"runtime_error:{type(exc).__name__}",
            )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "phase": PHASE,
                "max_event_age_seconds": self.max_event_age_seconds,
                "last_result": self._last_result.to_dict(),
                "stats": dict(self._stats),
                "safety": {
                    "vts_call": False,
                    "obs_call": False,
                    "tts_call": False,
                    "discord_send": False,
                    "game_input": False,
                    "explicit_vts_gate_required": True,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = snap["stats"]
        last = snap["last_result"]
        return [
            "Avatar Live Hook (STAGE-8L)",
            "  Mode: plan-only | can_act=False | vts_call=False",
            f"  Max event age: {snap['max_event_age_seconds']:.1f}s",
            (
                "  Last: "
                f"ok={last.get('ok')} | action={last.get('action')} | "
                f"reason={last.get('reason')} | event={last.get('event')} | "
                f"expression={last.get('expression')} | hotkey={last.get('hotkey') or 'none'} | "
                f"runtime={last.get('runtime_action') or 'none'}/{last.get('runtime_status') or 'none'}"
            ),
            (
                "  Stats: "
                f"preview={stats.get('preview', 0)} | planned={stats.get('planned', 0)} | "
                f"blocked={stats.get('blocked', 0)} | stale={stats.get('stale', 0)} | "
                f"duplicate={stats.get('duplicate', 0)} | policy={stats.get('policy', 0)}"
            ),
            "  Commands: /avatar-live-hook-status | /avatar-live-hook-preview | /avatar-live-hook-plan",
            "  Safety: no VTS/OBS/TTS/Discord/game input; 8I explicit gate owns real dispatch.",
        ]


def get_avatar_live_hook() -> AvatarLiveHook:
    return AvatarLiveHook.get_instance()


def avatar_live_hook_status_lines() -> list[str]:
    return get_avatar_live_hook().status_lines()


def avatar_live_hook_preview_lines() -> list[str]:
    result = get_avatar_live_hook().evaluate_latest(dry_run=True)
    return _format_result("Preview", result)


def avatar_live_hook_plan_lines() -> list[str]:
    result = get_avatar_live_hook().evaluate_latest(dry_run=False)
    return _format_result("Plan", result)


def _format_result(label: str, result: AvatarLiveHookResult) -> list[str]:
    return [
        f"Avatar Live Hook {label} (STAGE-8L)",
        f"  OK: {result.ok} | action={result.action} | reason={result.reason} | dry_run={result.dry_run}",
        f"  Event: {result.event} | source={result.source or 'none'} | viewer={result.viewer_name or 'none'} | age={result.event_age_seconds:.1f}s",
        f"  Expression: {result.expression} | hotkey={result.hotkey or 'none'}",
        f"  Body: {result.body_language} | duration={result.duration_s:.1f}s",
        f"  VTS call: {result.vts_call}",
        f"  Semantic runtime: {result.runtime_action or 'none'} | status={result.runtime_status or 'none'}",
        "  Safety: plan-only; no VTS/OBS/TTS/Discord/game input.",
    ]
