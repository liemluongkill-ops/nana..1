"""STAGE-8G: Auto-lite starter scheduler.

This layer is deliberately small and conservative.

It may automatically create, approve, and send a public starter only when:
  - stream policy says Nana is on a public stage and may proactively speak
  - the scheduler is enabled for this process
  - STAGE-8F controlled starter send is also enabled
  - there is no pending/approved starter proposal already waiting
  - scheduler cooldown and quota allow it

It does not call TTS, VTS, OBS, subtitle, or game input. Delivery still goes
through STAGE-8F's Discord outbox gate.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import asdict, dataclass
from types import SimpleNamespace
from typing import Optional

from nana.runtime.starter_proposals import (
    ProposalState,
    generate_starter_proposal,
    get_proposal_store,
)
from nana.runtime.starter_send import get_starter_send_controller


PHASE = "STAGE-8G"
DEFAULT_TICK_INTERVAL_SECONDS = 30.0
DEFAULT_COOLDOWN_SECONDS = 300.0
DEFAULT_QUOTA_WINDOW_SECONDS = 1800.0
DEFAULT_QUOTA_MAX = 2


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


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class StarterAutoResult:
    ok: bool
    action: str
    reason: str
    proposal_id: str = ""
    event_id: str = ""
    text: str = ""
    dry_run: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class StarterAutoScheduler:
    """Session-only auto-lite scheduler for public stage starters."""

    _instance: Optional["StarterAutoScheduler"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        tick_interval_seconds: Optional[float] = None,
        cooldown_seconds: Optional[float] = None,
        quota_window_seconds: Optional[float] = None,
        quota_max: Optional[int] = None,
    ) -> None:
        self.enabled = (
            _env_bool("NANA_STAGE_AUTO_STARTER_ENABLED", False)
            if enabled is None
            else bool(enabled)
        )
        self.tick_interval_seconds = (
            _env_float("NANA_STAGE_AUTO_TICK_SECONDS", DEFAULT_TICK_INTERVAL_SECONDS)
            if tick_interval_seconds is None
            else float(tick_interval_seconds)
        )
        self.cooldown_seconds = (
            _env_float("NANA_STAGE_AUTO_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)
            if cooldown_seconds is None
            else float(cooldown_seconds)
        )
        self.quota_window_seconds = (
            _env_float("NANA_STAGE_AUTO_QUOTA_WINDOW_SECONDS", DEFAULT_QUOTA_WINDOW_SECONDS)
            if quota_window_seconds is None
            else float(quota_window_seconds)
        )
        self.quota_max = (
            _env_int("NANA_STAGE_AUTO_QUOTA_MAX", DEFAULT_QUOTA_MAX)
            if quota_max is None
            else int(quota_max)
        )
        self._lock = threading.RLock()
        self._send_times: list[float] = []
        self._last_attempt_at = 0.0
        self._last_success_at = 0.0
        self._last_result = StarterAutoResult(False, "none", "none")
        self._stats = {
            "attempts": 0,
            "sent": 0,
            "dry_run": 0,
            "blocked": 0,
            "disabled": 0,
            "policy_blocked": 0,
            "send_gate_blocked": 0,
            "cooldown": 0,
            "quota": 0,
            "proposal_blocked": 0,
            "send_failed": 0,
        }
        self._worker: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    @classmethod
    def get_instance(cls) -> "StarterAutoScheduler":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            if cls._instance is not None:
                cls._instance.stop_worker()
            cls._instance = None

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.enabled = bool(enabled)

    def start_worker(self) -> bool:
        with self._lock:
            if self._worker is not None and self._worker.is_alive():
                return False
            self._stop_event.clear()
            self._worker = threading.Thread(
                target=self._run_worker,
                daemon=True,
                name="starter-auto-scheduler",
            )
            self._worker.start()
            return True

    def stop_worker(self) -> bool:
        with self._lock:
            worker = self._worker
            if worker is None or not worker.is_alive():
                self._stop_event.set()
                self._worker = None
                return False
            self._stop_event.set()
        worker.join(timeout=2.0)
        with self._lock:
            self._worker = None
        return True

    def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                self.tick(dry_run=False)
            except Exception as exc:
                self._record_result(
                    StarterAutoResult(False, "error", f"worker_error:{type(exc).__name__}")
                )
            self._stop_event.wait(timeout=max(1.0, self.tick_interval_seconds))

    def _record_result(self, result: StarterAutoResult, *, stat: str = "") -> StarterAutoResult:
        with self._lock:
            if not result.dry_run:
                self._stats["attempts"] += 1
                self._last_attempt_at = time.time()
            if result.ok and result.action == "sent":
                self._stats["sent"] += 1
                self._last_success_at = time.time()
                self._send_times.append(self._last_success_at)
            elif result.dry_run:
                self._stats["dry_run"] += 1
            else:
                self._stats["blocked"] += 1
                if stat in self._stats:
                    self._stats[stat] += 1
            self._last_result = result
        return result

    def _fresh_send_times(self, now: float) -> list[float]:
        cutoff = now - self.quota_window_seconds
        self._send_times = [item for item in self._send_times if item >= cutoff]
        return list(self._send_times)

    def _scheduler_quota_ok(self, now: float) -> tuple[bool, str, str]:
        with self._lock:
            recent = self._fresh_send_times(now)
            if self._last_success_at and now - self._last_success_at < self.cooldown_seconds:
                remaining = self.cooldown_seconds - (now - self._last_success_at)
                return False, f"auto_cooldown:{remaining:.0f}s", "cooldown"
            if len(recent) >= self.quota_max:
                return False, f"auto_quota:{len(recent)}/{self.quota_max}", "quota"
        return True, "ok", ""

    def _policy_ok(self) -> tuple[bool, str, object | None]:
        try:
            from nana.runtime.stream_state import get_stream_state

            policy = get_stream_state().get_policy()
        except Exception as exc:
            return False, f"policy_error:{type(exc).__name__}", None

        state = getattr(getattr(policy, "state", None), "value", "offline")
        if state not in {"live_idle", "intermission"}:
            return False, f"stream_state_blocked:{state}", policy
        if not getattr(policy, "can_proactive", False):
            return False, "policy_can_proactive_false", policy
        if not getattr(policy, "can_reply", False):
            return False, "policy_can_reply_false", policy
        if getattr(policy, "can_use_private_memory", False):
            return False, "policy_private_memory_true", policy
        error_type = getattr(getattr(policy, "error_type", None), "value", "none")
        if error_type == "bridge_down":
            return False, "bridge_down", policy
        return True, "ok", policy

    def _send_gate_ok(self) -> tuple[bool, str]:
        snap = get_starter_send_controller().snapshot()
        if not snap.get("enabled"):
            return False, "starter_send_disabled"
        if snap.get("channel_id") in {"", None, "missing"}:
            return False, "starter_send_missing_channel"
        return True, "ok"

    def _proposal_queue_ok(self) -> tuple[bool, str]:
        snap = get_proposal_store().snapshot()
        if int(snap.get("pending", 0)) > 0:
            return False, "pending_proposal_exists"
        if int(snap.get("approved", 0)) > 0:
            return False, "approved_proposal_waiting"
        return True, "ok"

    def tick(self, *, dry_run: bool = False) -> StarterAutoResult:
        """Run one auto-lite scheduling attempt.

        dry_run=True checks gates only and does not create/send a proposal.
        """
        now = time.time()

        if not dry_run and not self.enabled:
            return self._record_result(
                StarterAutoResult(False, "blocked", "auto_disabled"),
                stat="disabled",
            )

        policy_ok, policy_reason, policy = self._policy_ok()
        if not policy_ok:
            return self._record_result(
                StarterAutoResult(False, "blocked", policy_reason, dry_run=dry_run),
                stat="policy_blocked",
            )

        send_ok, send_reason = self._send_gate_ok()
        if not send_ok:
            return self._record_result(
                StarterAutoResult(False, "blocked", send_reason, dry_run=dry_run),
                stat="send_gate_blocked",
            )

        quota_ok, quota_reason, quota_stat = self._scheduler_quota_ok(now)
        if not quota_ok:
            return self._record_result(
                StarterAutoResult(False, "blocked", quota_reason, dry_run=dry_run),
                stat=quota_stat,
            )

        queue_ok, queue_reason = self._proposal_queue_ok()
        if not queue_ok:
            return self._record_result(
                StarterAutoResult(False, "blocked", queue_reason, dry_run=dry_run),
                stat="proposal_blocked",
            )

        if dry_run:
            return self._record_result(
                StarterAutoResult(True, "would_send", "eligible", dry_run=True),
            )

        try:
            from nana.runtime.social_starters import get_social_starter

            starter_result = get_social_starter().generate_starter()
        except Exception as exc:
            return self._record_result(
                StarterAutoResult(False, "blocked", f"social_starter_error:{type(exc).__name__}"),
                stat="proposal_blocked",
            )

        if starter_result.get("blocked"):
            return self._record_result(
                StarterAutoResult(
                    False,
                    "blocked",
                    f"social_starter_blocked:{starter_result.get('blocked_reason') or 'unknown'}",
                ),
                stat="proposal_blocked",
            )
        if not starter_result.get("starter"):
            return self._record_result(
                StarterAutoResult(False, "blocked", "social_starter_empty"),
                stat="proposal_blocked",
            )

        # 8G owns the auto-lite cadence gate. Pass an explicit positive decision
        # into 8E so old 8B budget/chat state cannot change the reason after
        # 8G has already passed its own policy, cooldown, quota, and starter gates.
        proposal = generate_starter_proposal(
            policy=policy,
            proactive_decision=SimpleNamespace(should_proactive=True),
            starter_result=starter_result,
        )
        if proposal is None:
            return self._record_result(
                StarterAutoResult(False, "blocked", "proposal_not_created"),
                stat="proposal_blocked",
            )

        ok, reason = get_proposal_store().approve(proposal.id)
        if not ok:
            return self._record_result(
                StarterAutoResult(False, "blocked", f"approve_failed:{reason}", proposal_id=proposal.id),
                stat="proposal_blocked",
            )

        result = get_starter_send_controller().send_proposal(proposal.id, dry_run=False)
        if not result.ok:
            return self._record_result(
                StarterAutoResult(
                    False,
                    "blocked",
                    f"send_failed:{result.reason}",
                    proposal_id=proposal.id,
                    text=proposal.text,
                ),
                stat="send_failed",
            )

        try:
            from nana.runtime.proactive_engine import get_proactive_engine

            get_proactive_engine().record_proactive(topic=proposal.topic)
        except Exception:
            pass

        return self._record_result(
            StarterAutoResult(
                True,
                "sent",
                "queued_outbox",
                proposal_id=proposal.id,
                event_id=result.event_id,
                text=proposal.text,
            )
        )

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            recent = self._fresh_send_times(now)
            cooldown_remaining = 0.0
            if self._last_success_at:
                cooldown_remaining = max(0.0, self.cooldown_seconds - (now - self._last_success_at))
            worker_running = self._worker is not None and self._worker.is_alive()
            return {
                "phase": PHASE,
                "enabled": self.enabled,
                "worker_running": worker_running,
                "tick_interval_seconds": self.tick_interval_seconds,
                "cooldown_remaining": cooldown_remaining,
                "cooldown_seconds": self.cooldown_seconds,
                "quota_used": len(recent),
                "quota_max": self.quota_max,
                "quota_window_seconds": self.quota_window_seconds,
                "stats": dict(self._stats),
                "last_result": self._last_result.to_dict(),
                "safety": {
                    "requires_starter_send_enabled": True,
                    "public_stage_only": True,
                    "no_private_memory": True,
                    "no_tts_vts_obs_game": True,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = snap["stats"]
        last = snap["last_result"]
        return [
            "Starter Auto-lite Scheduler (STAGE-8G)",
            f"  Enabled: {snap['enabled']} | worker={snap['worker_running']} | interval={snap['tick_interval_seconds']:.0f}s",
            f"  Cooldown: {snap['cooldown_remaining']:.0f}s/{snap['cooldown_seconds']:.0f}s | quota={snap['quota_used']}/{snap['quota_max']} per {snap['quota_window_seconds']:.0f}s",
            f"  Stats: attempts={stats['attempts']} | sent={stats['sent']} | dry_run={stats['dry_run']} | blocked={stats['blocked']} | disabled={stats['disabled']} | policy={stats['policy_blocked']} | send_gate={stats['send_gate_blocked']} | cooldown={stats['cooldown']} | quota={stats['quota']}",
            f"  Last: ok={last.get('ok')} | action={last.get('action')} | reason={last.get('reason')} | proposal={last.get('proposal_id') or 'none'} | event={last.get('event_id') or 'none'}",
            "  Commands: /starter-auto-enable | /starter-auto-disable | /starter-auto-preview | /starter-auto-tick | /starter-auto-worker-start | /starter-auto-worker-stop",
            "  Safety: requires 8F send enabled | public-stage only | no private memory | no TTS/VTS/OBS/game input",
        ]


def get_starter_auto_scheduler() -> StarterAutoScheduler:
    return StarterAutoScheduler.get_instance()


def starter_auto_status_lines() -> list[str]:
    return get_starter_auto_scheduler().status_lines()


def starter_auto_enable_lines() -> list[str]:
    scheduler = get_starter_auto_scheduler()
    scheduler.set_enabled(True)
    return ["  [8G] Starter auto-lite: ENABLED for this Nana process."]


def starter_auto_disable_lines() -> list[str]:
    scheduler = get_starter_auto_scheduler()
    scheduler.set_enabled(False)
    return ["  [8G] Starter auto-lite: DISABLED."]


def starter_auto_preview_lines() -> list[str]:
    result = get_starter_auto_scheduler().tick(dry_run=True)
    return _format_auto_result("Preview", result)


def starter_auto_tick_lines() -> list[str]:
    result = get_starter_auto_scheduler().tick(dry_run=False)
    return _format_auto_result("Tick", result)


def starter_auto_worker_start_lines() -> list[str]:
    scheduler = get_starter_auto_scheduler()
    started = scheduler.start_worker()
    state = "STARTED" if started else "already running"
    return [f"  [8G] Starter auto worker: {state}."]


def starter_auto_worker_stop_lines() -> list[str]:
    scheduler = get_starter_auto_scheduler()
    stopped = scheduler.stop_worker()
    state = "STOPPED" if stopped else "not running"
    return [f"  [8G] Starter auto worker: {state}."]


def _format_auto_result(label: str, result: StarterAutoResult) -> list[str]:
    lines = [
        f"Starter Auto {label} (STAGE-8G)",
        f"  OK: {result.ok} | action={result.action} | reason={result.reason} | dry_run={result.dry_run}",
        f"  Proposal: {result.proposal_id or 'none'}",
        f"  Event: {result.event_id or 'none'}",
    ]
    if result.text:
        lines.append(f"  Text: \"{result.text[:180]}\"")
    lines.append("  Safety: auto-lite gated by 8F | no TTS/VTS/OBS/game input")
    return lines
