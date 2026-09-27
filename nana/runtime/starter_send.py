"""STAGE-8F: Controlled starter proposal send/outbox.

This module is intentionally conservative:
  - approve does not send
  - send is disabled until explicitly enabled for this Nana process
  - send writes a Discord outbox JSON only, with no TTS/VTS/OBS/game calls
  - Discord bridge has its own kill switch before it will consume outbox files
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from nana.runtime.starter_proposals import (
    ProposalState,
    get_proposal_store,
    validate_public_proposal_text,
)


PHASE = "STAGE-8F"
DEFAULT_OUTBOX_DIR = Path(__file__).resolve().parent.parent / "data" / "external_bridge" / "outbox"
DEFAULT_COOLDOWN_SECONDS = 60.0
DEFAULT_QUOTA_WINDOW_SECONDS = 600.0
DEFAULT_QUOTA_MAX = 3


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


def _target_channel_id() -> str:
    return (
        os.getenv("NANA_STAGE_OUTBOX_CHANNEL_ID")
        or os.getenv("DISCORD_OUTBOX_CHANNEL_ID")
        or os.getenv("DISCORD_ALLOWED_TEXT_CHANNEL_ID")
        or ""
    ).strip()


def _target_guild_id() -> int | None:
    value = (
        os.getenv("NANA_STAGE_OUTBOX_GUILD_ID")
        or os.getenv("DISCORD_OUTBOX_GUILD_ID")
        or os.getenv("DISCORD_ALLOWED_GUILD_ID")
        or ""
    ).strip()
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _target_channel_name() -> str:
    return (
        os.getenv("NANA_STAGE_OUTBOX_CHANNEL_NAME")
        or os.getenv("DISCORD_OUTBOX_CHANNEL_NAME")
        or os.getenv("DISCORD_ALLOWED_TEXT_CHANNEL_NAME")
        or ""
    ).strip().lstrip("#")


def _outbox_dir() -> Path:
    return Path(os.getenv("NANA_STAGE_OUTBOX_DIR") or DEFAULT_OUTBOX_DIR)


def _atomic_json_write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, path)
    finally:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass


@dataclass(frozen=True)
class StarterOutboxEvent:
    event_id: str
    proposal_id: str
    text: str
    channel_id: str
    guild_id: Optional[int] = None
    channel_name: str = ""
    source: str = "starter_proposal"
    phase: str = PHASE
    created_at: float = field(default_factory=time.time)
    stream_state: str = "unknown"
    interaction_tone: str = "quiet"
    auto_send: bool = False
    approved_by_owner: bool = True
    speak: bool = False
    local_playback: bool = False
    audio_paths: list[str] = field(default_factory=list)
    safety: dict = field(
        default_factory=lambda: {
            "controlled_send": True,
            "no_tts_vts_obs_game": True,
            "text_only": True,
        }
    )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StarterSendResult:
    ok: bool
    reason: str
    proposal_id: str = ""
    event_id: str = ""
    outbox_path: str = ""
    text: str = ""
    dry_run: bool = False


class StarterSendController:
    """Session-only controlled send gate for approved starter proposals."""

    _instance: Optional["StarterSendController"] = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        outbox_dir: Path | None = None,
        channel_id: str | None = None,
        enabled: bool | None = None,
        cooldown_seconds: float | None = None,
        quota_window_seconds: float | None = None,
        quota_max: int | None = None,
    ) -> None:
        self.outbox_dir = outbox_dir or _outbox_dir()
        self.channel_id = channel_id if channel_id is not None else _target_channel_id()
        self.guild_id = _target_guild_id()
        self.channel_name = _target_channel_name()
        self.enabled = _env_bool("NANA_STAGE_CONTROLLED_SEND_ENABLED", False) if enabled is None else enabled
        self.cooldown_seconds = (
            _env_float("NANA_STAGE_SEND_COOLDOWN_SECONDS", DEFAULT_COOLDOWN_SECONDS)
            if cooldown_seconds is None
            else cooldown_seconds
        )
        self.quota_window_seconds = (
            _env_float("NANA_STAGE_SEND_QUOTA_WINDOW_SECONDS", DEFAULT_QUOTA_WINDOW_SECONDS)
            if quota_window_seconds is None
            else quota_window_seconds
        )
        self.quota_max = (
            _env_int("NANA_STAGE_SEND_QUOTA_MAX", DEFAULT_QUOTA_MAX)
            if quota_max is None
            else quota_max
        )
        self._lock = threading.Lock()
        self._send_times: list[float] = []
        self._last_send_at = 0.0
        self._stats = {
            "sent": 0,
            "dry_run": 0,
            "blocked": 0,
            "disabled": 0,
            "cooldown": 0,
            "quota": 0,
            "guard_blocked": 0,
            "policy_blocked": 0,
        }
        self._last_result: StarterSendResult = StarterSendResult(False, "none")

    @classmethod
    def get_instance(cls) -> "StarterSendController":
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

    def _block(self, reason: str, proposal_id: str = "", *, stat: str = "blocked") -> StarterSendResult:
        result = StarterSendResult(False, reason, proposal_id=proposal_id)
        with self._lock:
            self._stats["blocked"] += 1
            if stat in self._stats:
                self._stats[stat] += 1
            self._last_result = result
        return result

    def _policy_ok(self) -> tuple[bool, str, object | None]:
        try:
            from nana.runtime.stream_state import get_stream_state

            policy = get_stream_state().get_policy()
        except Exception as exc:
            return False, f"policy_error:{type(exc).__name__}", None

        state = getattr(getattr(policy, "state", None), "value", "offline")
        if state not in {"live_idle", "live_active", "intermission"}:
            return False, f"stream_state_blocked:{state}", policy
        if not getattr(policy, "can_reply", False):
            return False, "policy_can_reply_false", policy
        if getattr(policy, "can_auto_send", False):
            return False, "policy_auto_send_unexpected_true", policy
        if getattr(policy, "can_use_private_memory", False):
            return False, "policy_private_memory_true", policy
        error_type = getattr(getattr(policy, "error_type", None), "value", "none")
        if error_type == "bridge_down":
            return False, "bridge_down", policy
        return True, "ok", policy

    def _quota_ok(self, now: float) -> tuple[bool, str]:
        with self._lock:
            self._send_times = [
                item for item in self._send_times
                if now - item <= self.quota_window_seconds
            ]
            if self._last_send_at and now - self._last_send_at < self.cooldown_seconds:
                remaining = self.cooldown_seconds - (now - self._last_send_at)
                return False, f"cooldown:{remaining:.0f}s"
            if len(self._send_times) >= self.quota_max:
                return False, f"quota:{len(self._send_times)}/{self.quota_max}"
        return True, "ok"

    def send_proposal(self, proposal_id: str, *, dry_run: bool = False) -> StarterSendResult:
        proposal_id = (proposal_id or "").strip()
        if not proposal_id:
            return self._block("missing_proposal_id")

        if not dry_run and not self.enabled:
            return self._block("send_disabled", proposal_id, stat="disabled")

        store = get_proposal_store()
        proposal = store.get_proposal(proposal_id)
        if proposal is None:
            return self._block("proposal_not_found", proposal_id)
        if proposal.state != ProposalState.APPROVED:
            return self._block(f"proposal_not_approved:{proposal.state}", proposal_id)
        now = time.time()
        if now >= proposal.expires_at:
            store.expire_if_due(proposal.id, now=now)
            return self._block("proposal_expired", proposal_id)

        guard_ok, guard_reason = validate_public_proposal_text(proposal.text)
        if not guard_ok:
            return self._block(f"guard_blocked:{guard_reason}", proposal_id, stat="guard_blocked")

        policy_ok, policy_reason, policy = self._policy_ok()
        if not policy_ok:
            return self._block(policy_reason, proposal_id, stat="policy_blocked")

        if not self.channel_id:
            return self._block("missing_channel_id", proposal_id)

        if not dry_run:
            quota_ok, quota_reason = self._quota_ok(now)
            if not quota_ok:
                stat = "cooldown" if quota_reason.startswith("cooldown") else "quota"
                return self._block(quota_reason, proposal_id, stat=stat)

        state = getattr(getattr(policy, "state", None), "value", "unknown")
        tone = getattr(getattr(policy, "interaction_tone", None), "value", "quiet")
        event_id = f"starter-{uuid.uuid4().hex}"
        event = StarterOutboxEvent(
            event_id=event_id,
            proposal_id=proposal.id,
            text=proposal.text,
            channel_id=self.channel_id,
            guild_id=self.guild_id,
            channel_name=self.channel_name,
            stream_state=state,
            interaction_tone=tone,
        )
        path = self.outbox_dir / f"{event_id}.json"

        if dry_run:
            result = StarterSendResult(
                True,
                "dry_run",
                proposal_id=proposal.id,
                event_id=event_id,
                outbox_path=str(path),
                text=proposal.text,
                dry_run=True,
            )
            with self._lock:
                self._stats["dry_run"] += 1
                self._last_result = result
            return result

        _atomic_json_write(path, event.to_dict())
        mark_ok, mark_reason = store.mark_sent(proposal.id, event_id)
        if not mark_ok:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return self._block(f"mark_sent_failed:{mark_reason}", proposal_id)

        try:
            from nana.runtime.avatar_event_bridge import get_avatar_event_bridge

            get_avatar_event_bridge().observe_starter_event(
                text=proposal.text,
                proposal_id=proposal.id,
                event_id=event_id,
                channel=self.channel_name or self.channel_id,
            )
        except Exception:
            # Avatar event metadata must never block controlled text delivery.
            pass

        result = StarterSendResult(
            True,
            "queued_outbox",
            proposal_id=proposal.id,
            event_id=event_id,
            outbox_path=str(path),
            text=proposal.text,
        )
        with self._lock:
            self._send_times.append(now)
            self._last_send_at = now
            self._stats["sent"] += 1
            self._last_result = result
        return result

    def snapshot(self) -> dict:
        now = time.time()
        with self._lock:
            recent = [
                item for item in self._send_times
                if now - item <= self.quota_window_seconds
            ]
            cooldown_remaining = 0.0
            if self._last_send_at:
                cooldown_remaining = max(0.0, self.cooldown_seconds - (now - self._last_send_at))
            return {
                "phase": PHASE,
                "enabled": self.enabled,
                "outbox_dir": str(self.outbox_dir),
                "channel_id": self.channel_id or "missing",
                "guild_id": self.guild_id or "missing",
                "channel_name": self.channel_name or "missing",
                "cooldown_remaining": cooldown_remaining,
                "quota_used": len(recent),
                "quota_max": self.quota_max,
                "quota_window_seconds": self.quota_window_seconds,
                "stats": dict(self._stats),
                "last_result": asdict(self._last_result),
                "safety": {
                    "auto_send": False,
                    "no_tts_vts_obs_game": True,
                    "text_only": True,
                },
            }

    def status_lines(self) -> list[str]:
        snap = self.snapshot()
        stats = snap["stats"]
        last = snap["last_result"]
        return [
            "📨 Starter Controlled Send (STAGE-8F)",
            f"  Enabled: {snap['enabled']} | auto_send=False | outbox={snap['outbox_dir']}",
            f"  Target: guild={snap['guild_id']} | channel_id={snap['channel_id']} | channel_name={snap['channel_name']}",
            f"  Cooldown: {snap['cooldown_remaining']:.0f}s | quota={snap['quota_used']}/{snap['quota_max']} per {snap['quota_window_seconds']:.0f}s",
            f"  Stats: sent={stats['sent']} | dry_run={stats['dry_run']} | blocked={stats['blocked']} | disabled={stats['disabled']} | cooldown={stats['cooldown']} | quota={stats['quota']}",
            f"  Last: ok={last.get('ok')} | reason={last.get('reason')} | proposal={last.get('proposal_id') or 'none'} | event={last.get('event_id') or 'none'}",
            "  Commands: /starter-send-enable | /starter-send-disable | /starter-send-status | /starter-proposal-send <id> | /starter-proposal-send-preview <id>",
            "  Safety: explicit-send only | no TTS/VTS/OBS/game input | bridge outbox has separate kill switch",
        ]


def get_starter_send_controller() -> StarterSendController:
    return StarterSendController.get_instance()


def starter_send_status_lines() -> list[str]:
    return get_starter_send_controller().status_lines()


def starter_send_enable_lines() -> list[str]:
    controller = get_starter_send_controller()
    controller.set_enabled(True)
    return ["  [8F] Starter controlled send: ENABLED for this Nana process."]


def starter_send_disable_lines() -> list[str]:
    controller = get_starter_send_controller()
    controller.set_enabled(False)
    return ["  [8F] Starter controlled send: DISABLED."]


def starter_send_preview_lines(proposal_id: str) -> list[str]:
    result = get_starter_send_controller().send_proposal(proposal_id, dry_run=True)
    return _format_result("Preview", result)


def starter_send_lines(proposal_id: str) -> list[str]:
    result = get_starter_send_controller().send_proposal(proposal_id, dry_run=False)
    return _format_result("Send", result)


def _format_result(label: str, result: StarterSendResult) -> list[str]:
    lines = [
        f"📨 Starter {label} (STAGE-8F)",
        f"  OK: {result.ok} | reason={result.reason} | dry_run={result.dry_run}",
        f"  Proposal: {result.proposal_id or 'none'}",
        f"  Event: {result.event_id or 'none'}",
    ]
    if result.outbox_path:
        lines.append(f"  Outbox: {result.outbox_path}")
    if result.text:
        lines.append(f"  Text: \"{result.text[:180]}\"")
    lines.append("  Safety: no auto-send | no TTS/VTS/OBS/game input")
    return lines
