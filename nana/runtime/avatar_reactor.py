"""STAGE-8H: Avatar reaction wiring preview.

This layer turns an 8C AvatarDirective into a renderer-ready reaction plan.
It is intentionally preview-first: by default it never calls VTS. A later stage
may attach the real renderer behind an explicit gate.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Optional

from nana.runtime.avatar_director import AvatarDirective, get_avatar_directive


PHASE = "STAGE-8H"
DEFAULT_COOLDOWN_SECONDS = 2.5


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class AvatarReactionPlan:
    ok: bool
    action: str
    reason: str
    expression: str = "neutral"
    hotkey: str = ""
    body_language: str = "minimal"
    duration_s: float = 0.0
    chat_event: str = "none"
    dry_run: bool = True
    vts_call: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


_HOTKEY_MAP = {
    "neutral": "",
    "normal": "",
    "minimal": "",
    "happy": "星星眼",
    "excited": "星星眼",
    "surprised": "惊讶",
    "concerned": "皱眉",
    "sleepy": "困",
    "relaxed": "微笑",
}


class AvatarReactionController:
    """Session-only avatar reaction controller.

    It converts 8C directives to renderer plans and tracks cooldown/statistics.
    Real VTS dispatch remains disabled unless explicitly allowed by a future
    integration path.
    """

    _instance: Optional["AvatarReactionController"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        cooldown_seconds: Optional[float] = None,
        allow_vts_call: Optional[bool] = None,
    ) -> None:
        self.enabled = (
            _env_bool("NANA_AVATAR_REACTION_ENABLED", True)
            if enabled is None
            else bool(enabled)
        )
        self.cooldown_seconds = (
            _env_float("NANA_AVATAR_REACTION_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)
            if cooldown_seconds is None
            else float(cooldown_seconds)
        )
        self.allow_vts_call = (
            _env_bool("NANA_AVATAR_REACTION_ALLOW_VTS", False)
            if allow_vts_call is None
            else bool(allow_vts_call)
        )
        self._lock = threading.RLock()
        self._last_reaction_at = 0.0
        self._last_plan = AvatarReactionPlan(False, "none", "none")
        self._stats = {
            "planned": 0,
            "dry_run": 0,
            "blocked": 0,
            "cooldown": 0,
            "disabled": 0,
            "policy_blocked": 0,
            "vts_call": 0,
        }

    @classmethod
    def get_instance(cls) -> "AvatarReactionController":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.enabled = bool(enabled)

    def set_vts_allowed(self, allowed: bool) -> None:
        with self._lock:
            self.allow_vts_call = bool(allowed)

    def _record(self, plan: AvatarReactionPlan, *, stat: str = "") -> AvatarReactionPlan:
        with self._lock:
            if plan.ok:
                self._stats["planned"] += 1
                if plan.dry_run:
                    self._stats["dry_run"] += 1
                if plan.vts_call:
                    self._stats["vts_call"] += 1
                if not plan.dry_run:
                    self._last_reaction_at = time.time()
            else:
                self._stats["blocked"] += 1
                if stat in self._stats:
                    self._stats[stat] += 1
            self._last_plan = plan
        return plan

    def _cooldown_ok(self, now: float) -> tuple[bool, str]:
        with self._lock:
            if self._last_reaction_at and now - self._last_reaction_at < self.cooldown_seconds:
                remaining = self.cooldown_seconds - (now - self._last_reaction_at)
                return False, f"cooldown:{remaining:.1f}s"
        return True, "ok"

    def build_plan(
        self,
        *,
        chat_event: str = "message",
        directive: Optional[AvatarDirective] = None,
        dry_run: bool = True,
    ) -> AvatarReactionPlan:
        now = time.time()

        if not dry_run and not self.enabled:
            return self._record(
                AvatarReactionPlan(False, "blocked", "disabled", chat_event=chat_event, dry_run=dry_run),
                stat="disabled",
            )

        if directive is None:
            try:
                directive = get_avatar_directive(chat_event=chat_event)
            except Exception as exc:
                return self._record(
                    AvatarReactionPlan(
                        False,
                        "blocked",
                        f"directive_error:{type(exc).__name__}",
                        chat_event=chat_event,
                        dry_run=dry_run,
                    ),
                    stat="policy_blocked",
                )

        if not directive.can_avatar:
            return self._record(
                AvatarReactionPlan(
                    False,
                    "blocked",
                    directive.reason,
                    expression=directive.expression,
                    body_language=directive.body_language,
                    duration_s=directive.duration_s,
                    chat_event=chat_event,
                    dry_run=dry_run,
                ),
                stat="policy_blocked",
            )

        if not dry_run:
            cooldown_ok, cooldown_reason = self._cooldown_ok(now)
            if not cooldown_ok:
                return self._record(
                    AvatarReactionPlan(
                        False,
                        "blocked",
                        cooldown_reason,
                        expression=directive.expression,
                        body_language=directive.body_language,
                        duration_s=directive.duration_s,
                        chat_event=chat_event,
                        dry_run=dry_run,
                    ),
                    stat="cooldown",
                )

        expression = directive.expression or "neutral"
        hotkey = _HOTKEY_MAP.get(expression, "")
        would_vts = bool(hotkey and self.allow_vts_call and not dry_run)
        action = "would_react" if dry_run else "planned"
        reason = "preview_only" if dry_run else "vts_disabled_preview_plan"
        if would_vts:
            reason = "vts_allowed_but_not_dispatched_in_8H"

        return self._record(
            AvatarReactionPlan(
                True,
                action,
                reason,
                expression=expression,
                hotkey=hotkey,
                body_language=directive.body_language,
                duration_s=directive.duration_s,
                chat_event=chat_event,
                dry_run=dry_run,
                vts_call=False,
            )
        )

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            cooldown_remaining = 0.0
            if self._last_reaction_at:
                cooldown_remaining = max(0.0, self.cooldown_seconds - (now - self._last_reaction_at))
            return {
                "phase": PHASE,
                "enabled": self.enabled,
                "allow_vts_call": self.allow_vts_call,
                "cooldown_seconds": self.cooldown_seconds,
                "cooldown_remaining": cooldown_remaining,
                "last_plan": self._last_plan.to_dict(),
                "stats": dict(self._stats),
                "safety": {
                    "preview_first": True,
                    "no_vts_call": True,
                    "no_obs_game": True,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = snap["stats"]
        last = snap["last_plan"]
        return [
            "Avatar Reaction Wiring (STAGE-8H)",
            f"  Enabled: {snap['enabled']} | allow_vts_call={snap['allow_vts_call']} | cooldown={snap['cooldown_remaining']:.1f}s/{snap['cooldown_seconds']:.1f}s",
            f"  Stats: planned={stats['planned']} | dry_run={stats['dry_run']} | blocked={stats['blocked']} | cooldown={stats['cooldown']} | policy={stats['policy_blocked']} | vts_call={stats['vts_call']}",
            f"  Last: ok={last.get('ok')} | action={last.get('action')} | reason={last.get('reason')} | expression={last.get('expression')} | hotkey={last.get('hotkey') or 'none'}",
            "  Commands: /avatar-reaction-status | /avatar-reaction-preview <event> | /avatar-reaction-apply-preview <event>",
            "  Safety: preview-first | no VTS/OBS/game call in STAGE-8H",
        ]


def get_avatar_reaction_controller() -> AvatarReactionController:
    return AvatarReactionController.get_instance()


def avatar_reaction_status_lines() -> list[str]:
    return get_avatar_reaction_controller().status_lines()


def avatar_reaction_preview_lines(chat_event: str = "message") -> list[str]:
    plan = get_avatar_reaction_controller().build_plan(chat_event=chat_event, dry_run=True)
    return _format_plan("Preview", plan)


def avatar_reaction_apply_preview_lines(chat_event: str = "message") -> list[str]:
    plan = get_avatar_reaction_controller().build_plan(chat_event=chat_event, dry_run=False)
    return _format_plan("Apply Preview", plan)


def _format_plan(label: str, plan: AvatarReactionPlan) -> list[str]:
    return [
        f"Avatar Reaction {label} (STAGE-8H)",
        f"  OK: {plan.ok} | action={plan.action} | reason={plan.reason} | dry_run={plan.dry_run}",
        f"  Event: {plan.chat_event}",
        f"  Expression: {plan.expression} | hotkey={plan.hotkey or 'none'}",
        f"  Body: {plan.body_language} | duration={plan.duration_s:.1f}s",
        f"  VTS call: {plan.vts_call}",
        "  Safety: no VTS/OBS/game call in STAGE-8H",
    ]
