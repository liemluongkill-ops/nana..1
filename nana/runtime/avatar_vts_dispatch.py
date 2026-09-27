"""STAGE-8I: Controlled real VTS dispatch gate for avatar reactions.

This layer is the first place where 8H avatar reaction plans may become real
VTube Studio hotkey calls. It is explicit-enable only and fails closed by
default. Preview/status paths never call VTS.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any, Awaitable, Callable, Optional

from nana.runtime.avatar_reactor import AvatarReactionPlan, get_avatar_reaction_controller


PHASE = "STAGE-8I"
DEFAULT_COOLDOWN_SECONDS = 3.0


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
class AvatarVTSDispatchResult:
    ok: bool
    action: str
    reason: str
    event: str = "message"
    expression: str = "neutral"
    hotkey: str = ""
    dry_run: bool = True
    vts_call: bool = False
    request_id: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


class AvatarVTSDispatchGate:
    """Explicit gate for sending avatar reaction hotkeys to VTube Studio."""

    _instance: Optional["AvatarVTSDispatchGate"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        cooldown_seconds: Optional[float] = None,
        require_ready: bool = True,
        dispatch_fn: Optional[Callable[[Any, str, str], Awaitable[Any]]] = None,
        snapshot_fn: Optional[Callable[[Any], dict]] = None,
    ) -> None:
        self.enabled = (
            _env_bool("NANA_AVATAR_VTS_DISPATCH_ENABLED", False)
            if enabled is None
            else bool(enabled)
        )
        self.cooldown_seconds = (
            _env_float("NANA_AVATAR_VTS_DISPATCH_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)
            if cooldown_seconds is None
            else float(cooldown_seconds)
        )
        self.require_ready = bool(require_ready)
        self._dispatch_fn = dispatch_fn or _default_hotkey_dispatch
        self._snapshot_fn = snapshot_fn or _default_vts_snapshot
        self._lock = threading.RLock()
        self._last_dispatch_at = 0.0
        self._last_result = AvatarVTSDispatchResult(False, "none", "none")
        self._stats = {
            "preview": 0,
            "sent": 0,
            "blocked": 0,
            "disabled": 0,
            "policy": 0,
            "no_hotkey": 0,
            "not_ready": 0,
            "cooldown": 0,
            "error": 0,
        }

    @classmethod
    def get_instance(cls) -> "AvatarVTSDispatchGate":
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

    def _record(
        self,
        result: AvatarVTSDispatchResult,
        *,
        stat: str = "",
        mark_dispatch_time: bool = False,
    ) -> AvatarVTSDispatchResult:
        with self._lock:
            if result.dry_run and result.ok:
                self._stats["preview"] += 1
            elif result.ok and result.vts_call:
                self._stats["sent"] += 1
            else:
                self._stats["blocked"] += 1
                if stat in self._stats:
                    self._stats[stat] += 1
            if mark_dispatch_time:
                self._last_dispatch_at = time.time()
            self._last_result = result
        return result

    def _cooldown_ok(self, now: float) -> tuple[bool, str]:
        with self._lock:
            if self._last_dispatch_at and now - self._last_dispatch_at < self.cooldown_seconds:
                remaining = self.cooldown_seconds - (now - self._last_dispatch_at)
                return False, f"cooldown:{remaining:.1f}s"
        return True, "ok"

    def _vts_ready(self, vts: Any) -> tuple[bool, str, dict]:
        try:
            snap = self._snapshot_fn(vts)
        except Exception as exc:
            return False, f"snapshot_error:{type(exc).__name__}", {}
        if not self.require_ready:
            return True, "ready_not_required", snap
        if snap.get("ready"):
            return True, "ready", snap
        if not snap.get("connected"):
            return False, "vts_not_connected", snap
        return False, f"vts_not_ready:{snap.get('auth_status_label') or snap.get('auth_status')}", snap

    def preview(
        self,
        *,
        chat_event: str = "message",
        plan: Optional[AvatarReactionPlan] = None,
    ) -> AvatarVTSDispatchResult:
        plan = plan or get_avatar_reaction_controller().build_plan(chat_event=chat_event, dry_run=True)
        return self._build_result_from_plan(plan, dry_run=True, reason="preview_only")

    async def dispatch(
        self,
        *,
        vts: Any = None,
        chat_event: str = "message",
        plan: Optional[AvatarReactionPlan] = None,
        dry_run: bool = False,
    ) -> AvatarVTSDispatchResult:
        if dry_run:
            return self.preview(chat_event=chat_event, plan=plan)

        plan = plan or get_avatar_reaction_controller().build_plan(
            chat_event=chat_event,
            dry_run=False,
        )

        if not plan.ok:
            return self._record(
                self._build_result_from_plan(plan, dry_run=False, reason=f"reaction_plan_blocked:{plan.reason}"),
                stat="policy",
            )

        if not plan.hotkey:
            return self._record(
                self._build_result_from_plan(plan, dry_run=False, reason="no_hotkey"),
                stat="no_hotkey",
            )

        if not self.enabled:
            return self._record(
                self._build_result_from_plan(plan, dry_run=False, reason="dispatch_disabled"),
                stat="disabled",
            )

        cooldown_ok, cooldown_reason = self._cooldown_ok(time.time())
        if not cooldown_ok:
            return self._record(
                self._build_result_from_plan(plan, dry_run=False, reason=cooldown_reason),
                stat="cooldown",
            )

        ready, ready_reason, _snap = self._vts_ready(vts)
        if not ready:
            return self._record(
                self._build_result_from_plan(plan, dry_run=False, reason=ready_reason),
                stat="not_ready",
            )

        request_id = f"avatar8i_{plan.hotkey}_{int(time.time() * 1000)}"
        try:
            await self._dispatch_fn(vts, plan.hotkey, request_id)
        except Exception as exc:
            return self._record(
                AvatarVTSDispatchResult(
                    False,
                    "blocked",
                    "dispatch_error",
                    event=plan.chat_event,
                    expression=plan.expression,
                    hotkey=plan.hotkey,
                    dry_run=False,
                    vts_call=False,
                    request_id=request_id,
                    error=f"{type(exc).__name__}: {exc}",
                ),
                stat="error",
            )

        return self._record(
            AvatarVTSDispatchResult(
                True,
                "sent",
                "hotkey_dispatched",
                event=plan.chat_event,
                expression=plan.expression,
                hotkey=plan.hotkey,
                dry_run=False,
                vts_call=True,
                request_id=request_id,
            ),
            mark_dispatch_time=True,
        )

    def _build_result_from_plan(
        self,
        plan: AvatarReactionPlan,
        *,
        dry_run: bool,
        reason: str,
    ) -> AvatarVTSDispatchResult:
        ok = bool(plan.ok and plan.hotkey and (dry_run or reason == "preview_only"))
        action = "would_dispatch" if dry_run and ok else "blocked"
        return AvatarVTSDispatchResult(
            ok=ok,
            action=action,
            reason=reason,
            event=plan.chat_event,
            expression=plan.expression,
            hotkey=plan.hotkey,
            dry_run=dry_run,
            vts_call=False,
        )

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            cooldown_remaining = 0.0
            if self._last_dispatch_at:
                cooldown_remaining = max(0.0, self.cooldown_seconds - (now - self._last_dispatch_at))
            return {
                "phase": PHASE,
                "enabled": self.enabled,
                "cooldown_seconds": self.cooldown_seconds,
                "cooldown_remaining": cooldown_remaining,
                "require_ready": self.require_ready,
                "last_result": self._last_result.to_dict(),
                "stats": dict(self._stats),
                "safety": {
                    "explicit_enable_required": True,
                    "preview_no_vts_call": True,
                    "no_obs_game": True,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = snap["stats"]
        last = snap["last_result"]
        return [
            "Avatar VTS Dispatch Gate (STAGE-8I)",
            f"  Enabled: {snap['enabled']} | require_ready={snap['require_ready']} | cooldown={snap['cooldown_remaining']:.1f}s/{snap['cooldown_seconds']:.1f}s",
            f"  Stats: preview={stats['preview']} | sent={stats['sent']} | blocked={stats['blocked']} | disabled={stats['disabled']} | not_ready={stats['not_ready']} | cooldown={stats['cooldown']} | error={stats['error']}",
            f"  Last: ok={last.get('ok')} | action={last.get('action')} | reason={last.get('reason')} | expression={last.get('expression')} | hotkey={last.get('hotkey') or 'none'} | vts_call={last.get('vts_call')}",
            "  Commands: /avatar-vts-status | /avatar-vts-enable | /avatar-vts-disable | /avatar-vts-preview <event> | /avatar-vts-dispatch <event>",
            "  Safety: explicit-enable only | preview has no VTS call | no OBS/game input",
        ]


async def _default_hotkey_dispatch(vts: Any, hotkey: str, request_id: str) -> Any:
    if not vts:
        raise RuntimeError("missing_vts_runtime")
    return await vts.request(
        {
            "apiName": "VTubeStudioPublicAPI",
            "apiVersion": "1.0",
            "requestID": request_id,
            "messageType": "HotkeyTriggerRequest",
            "data": {"hotkeyID": hotkey},
        }
    )


def _default_vts_snapshot(vts: Any) -> dict:
    from nana.integrations.vts import get_vts_runtime, vts_snapshot

    return vts_snapshot(vts or get_vts_runtime())


def get_avatar_vts_dispatch_gate() -> AvatarVTSDispatchGate:
    return AvatarVTSDispatchGate.get_instance()


def avatar_vts_status_lines() -> list[str]:
    return get_avatar_vts_dispatch_gate().status_lines()


def avatar_vts_enable_lines() -> list[str]:
    gate = get_avatar_vts_dispatch_gate()
    gate.set_enabled(True)
    return ["  [8I] Avatar VTS dispatch: ENABLED for this Nana process."]


def avatar_vts_disable_lines() -> list[str]:
    gate = get_avatar_vts_dispatch_gate()
    gate.set_enabled(False)
    return ["  [8I] Avatar VTS dispatch: DISABLED."]


def avatar_vts_preview_lines(chat_event: str = "message") -> list[str]:
    result = get_avatar_vts_dispatch_gate().preview(chat_event=chat_event)
    return _format_result("Preview", result)


async def avatar_vts_dispatch_lines(vts: Any = None, chat_event: str = "message") -> list[str]:
    result = await get_avatar_vts_dispatch_gate().dispatch(vts=vts, chat_event=chat_event)
    return _format_result("Dispatch", result)


def _format_result(label: str, result: AvatarVTSDispatchResult) -> list[str]:
    return [
        f"Avatar VTS {label} (STAGE-8I)",
        f"  OK: {result.ok} | action={result.action} | reason={result.reason} | dry_run={result.dry_run}",
        f"  Event: {result.event}",
        f"  Expression: {result.expression} | hotkey={result.hotkey or 'none'}",
        f"  VTS call: {result.vts_call} | request_id={result.request_id or 'none'}",
        f"  Error: {result.error or 'none'}",
        "  Safety: explicit-enable only | no OBS/game input",
    ]
