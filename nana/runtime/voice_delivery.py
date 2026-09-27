"""STAGE-9Y: voice delivery planner.

This layer is different from STAGE-9T voice reply budget.  STAGE-9T decides
whether text should be shortened for local TTS.  STAGE-9Y decides how a full
reply should be packeted so Nana can start speaking early without losing the
tail of long story replies.

No TTS call happens here.  The module only produces read-only diagnostics and
small helper decisions for the chat pipeline.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re
import threading
import time
from typing import Any

from nana.core.format import shorten_line
from nana.runtime.voice_reply_budget import get_voice_reply_budget, voice_stream_dispatch_config


PHASE = "STAGE-9Y"


@dataclass(frozen=True)
class VoiceDeliveryChunk:
    index: int
    role: str
    text: str
    chars: int
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class VoiceDeliveryPlan:
    mode: str
    strategy: str
    original_text: str
    original_chars: int
    voice_chars: int
    omitted_chars: int
    chunks: tuple[VoiceDeliveryChunk, ...]
    lead_count: int
    tail_count: int
    tail_chars: int
    queue_state: str
    warnings: tuple[str, ...]
    config: dict[str, Any]
    phase: str = PHASE
    read_only: bool = True
    can_act: bool = False
    memory_write: bool = False
    api_call: bool = False
    tts_call: bool = False
    vts_call: bool = False
    obs_call: bool = False
    discord_call: bool = False
    game_input: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chunks"] = [chunk.to_dict() for chunk in self.chunks]
        return data


def _clean(value: Any, limit: int = 12000) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    return text[:limit]


def _sentence_units(text: str) -> list[str]:
    cleaned = _clean(text)
    if not cleaned:
        return []
    pieces = [piece.strip() for piece in re.split(r"(?<=[.!?。！？])\s+", cleaned) if piece.strip()]
    return pieces or [cleaned]


def _join_units(units: list[str]) -> str:
    return " ".join(unit.strip() for unit in units if unit and unit.strip()).strip()


def _build_lead_chunks(
    units: list[str],
    *,
    lead_segments: int,
    lead_min_chars: int,
    lead_max_chars: int,
) -> tuple[list[str], int]:
    chunks: list[str] = []
    index = 0
    total = len(units)
    for _ in range(max(0, int(lead_segments or 0))):
        if index >= total:
            break
        current: list[str] = []
        while index < total:
            candidate = _join_units([*current, units[index]])
            current_text = _join_units(current)
            if current and len(candidate) > lead_max_chars and len(current_text) >= lead_min_chars:
                break
            current.append(units[index])
            index += 1
            current_text = _join_units(current)
            if len(current_text) >= lead_min_chars:
                break
            if len(current_text) >= lead_max_chars:
                break
        text = _join_units(current)
        if text:
            chunks.append(text)
    return chunks, index


def _queue_state(voice: Any = None) -> tuple[str, dict[str, Any]]:
    if voice is None:
        return "unavailable", {}
    snap_fn = getattr(voice, "snapshot", None)
    if not callable(snap_fn):
        return "unavailable", {}
    try:
        runtime = dict(snap_fn())
    except Exception as exc:
        return "error", {"last_error": repr(exc)}
    queue_size = runtime.get("queue_size")
    queue_max = runtime.get("queue_maxsize")
    if queue_size is None or queue_max in {None, 0}:
        return "unavailable", runtime
    try:
        queue_size_i = int(queue_size)
        queue_max_i = int(queue_max)
    except (TypeError, ValueError):
        return "unavailable", runtime
    if queue_size_i >= queue_max_i:
        return "blocked_full", runtime
    if queue_size_i >= max(0, queue_max_i - 1):
        return "warn_near_full", runtime
    return "ready", runtime


def build_voice_delivery_plan(text: Any, *, mode: str = "story", voice: Any = None) -> VoiceDeliveryPlan:
    cleaned = _clean(text)
    config = voice_stream_dispatch_config(mode)
    queue_state, queue_runtime = _queue_state(voice)
    warnings: list[str] = []
    if not cleaned:
        plan = VoiceDeliveryPlan(
            mode=str(mode or "story"),
            strategy="empty",
            original_text="",
            original_chars=0,
            voice_chars=0,
            omitted_chars=0,
            chunks=(),
            lead_count=0,
            tail_count=0,
            tail_chars=0,
            queue_state=queue_state,
            warnings=(),
            config=config,
        )
        get_voice_delivery().record(plan)
        return plan

    chunks: list[VoiceDeliveryChunk] = []
    units = _sentence_units(cleaned)
    strategy = str(config.get("mode") or "stream_chunks")
    lead_segments = int(config.get("lead_segments") or 0)
    lead_min_chars = int(config.get("lead_min_chars") or 90)
    lead_max_chars = int(config.get("lead_max_chars") or 180)

    if strategy == "lead_then_tail" and lead_segments > 0:
        lead_texts, used_units = _build_lead_chunks(
            units,
            lead_segments=lead_segments,
            lead_min_chars=lead_min_chars,
            lead_max_chars=lead_max_chars,
        )
        for lead_text in lead_texts:
            chunks.append(
                VoiceDeliveryChunk(
                    index=len(chunks) + 1,
                    role="lead",
                    text=lead_text,
                    chars=len(lead_text),
                    reason="early_sentence_packet",
                )
            )
        tail_text = _join_units(units[used_units:])
        if tail_text:
            chunks.append(
                VoiceDeliveryChunk(
                    index=len(chunks) + 1,
                    role="tail",
                    text=tail_text,
                    chars=len(tail_text),
                    reason="full_remaining_tail",
                )
            )
    else:
        chunks.append(
            VoiceDeliveryChunk(
                index=1,
                role="single",
                text=cleaned,
                chars=len(cleaned),
                reason="stream_chunk_passthrough",
            )
        )

    delivered_text = _join_units([chunk.text for chunk in chunks])
    voice_chars = len(delivered_text)
    tail_chunks = [chunk for chunk in chunks if chunk.role == "tail"]
    tail_chars = sum(chunk.chars for chunk in tail_chunks)
    if tail_chars > 1200:
        warnings.append("large_tail_packet")
    if queue_state in {"blocked_full", "warn_near_full", "error"}:
        warnings.append(f"queue_{queue_state}")
    queue_max = queue_runtime.get("queue_maxsize")
    queue_size = queue_runtime.get("queue_size")
    try:
        free_slots = int(queue_max) - int(queue_size)
        if free_slots >= 0 and len(chunks) > free_slots:
            warnings.append("chunks_exceed_current_free_queue_slots")
    except (TypeError, ValueError):
        pass

    plan = VoiceDeliveryPlan(
        mode=str(mode or "story"),
        strategy=strategy,
        original_text=cleaned,
        original_chars=len(cleaned),
        voice_chars=voice_chars,
        omitted_chars=max(0, len(cleaned) - voice_chars),
        chunks=tuple(chunks),
        lead_count=sum(1 for chunk in chunks if chunk.role == "lead"),
        tail_count=len(tail_chunks),
        tail_chars=tail_chars,
        queue_state=queue_state,
        warnings=tuple(warnings),
        config=config,
    )
    get_voice_delivery().record(plan)
    return plan


def should_flush_voice_buffer(
    buffer_text: Any,
    *,
    mode: str,
    packets_sent: int,
    dispatch_config: dict[str, Any] | None = None,
    force: bool = False,
    sentence_boundary: bool = False,
    normal_flush_chars: int = 60,
) -> bool:
    text = _clean(buffer_text)
    if not text:
        return False
    if force:
        return True
    config = dict(dispatch_config or voice_stream_dispatch_config(mode))
    lead_segments = int(config.get("lead_segments") or 0)
    if lead_segments <= 0:
        return len(text) >= max(1, int(normal_flush_chars or 60))
    if int(packets_sent or 0) >= lead_segments:
        return False
    if not sentence_boundary:
        return False
    lead_min_chars = int(config.get("lead_min_chars") or 90)
    lead_max_chars = int(config.get("lead_max_chars") or max(lead_min_chars, 180))
    return len(text) >= min(max(1, lead_min_chars), max(1, lead_max_chars))


class VoiceDelivery:
    _instance: "VoiceDelivery | None" = None
    _instance_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._initialized_at = time.time()
        self._last_plan: VoiceDeliveryPlan | None = None
        self._stats: dict[str, int] = {
            "built": 0,
            "empty": 0,
            "lead_then_tail": 0,
            "stream_chunks": 0,
            "warnings": 0,
            "tail_full": 0,
        }

    @classmethod
    def get_instance(cls) -> "VoiceDelivery":
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_for_test(cls) -> None:
        with cls._instance_lock:
            cls._instance = None

    def record(self, plan: VoiceDeliveryPlan) -> None:
        with self._lock:
            self._stats["built"] += 1
            if not plan.original_text:
                self._stats["empty"] += 1
            if plan.strategy == "lead_then_tail":
                self._stats["lead_then_tail"] += 1
            else:
                self._stats["stream_chunks"] += 1
            if plan.warnings:
                self._stats["warnings"] += 1
            if plan.tail_count:
                self._stats["tail_full"] += 1
            self._last_plan = plan

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            last = self._last_plan.to_dict() if self._last_plan else {}
            return {
                "phase": PHASE,
                "mode": "voice-delivery-planner",
                "uptime_seconds": max(0.0, time.time() - self._initialized_at),
                "read_only": True,
                "can_act": False,
                "memory_write": False,
                "api_call": False,
                "tts_call": False,
                "vts_call": False,
                "obs_call": False,
                "discord_call": False,
                "game_input": False,
                "last_plan": last,
                "stats": dict(self._stats),
            }


def get_voice_delivery() -> VoiceDelivery:
    return VoiceDelivery.get_instance()


def _voice_runtime_summary(voice: Any = None) -> tuple[str, dict[str, Any]]:
    try:
        from nana.core.status_voice import _voice_status_snapshot

        snap = _voice_status_snapshot(voice)
        return "ok", snap
    except Exception as exc:
        return "error", {"error": repr(exc), "runtime": {}}


def voice_delivery_status_lines(voice: Any = None) -> list[str]:
    snap = get_voice_delivery().snapshot()
    stats = dict(snap.get("stats") or {})
    last = dict(snap.get("last_plan") or {})
    config = voice_stream_dispatch_config("full")
    runtime_state, voice_snap = _voice_runtime_summary(voice)
    runtime = dict(voice_snap.get("runtime") or {})
    queue_size = runtime.get("queue_size", "n/a")
    queue_max = runtime.get("queue_maxsize", "n/a")
    budget = get_voice_reply_budget().snapshot()
    budget_last = dict(budget.get("last_result") or {})
    warnings = ",".join(last.get("warnings") or []) or "none"
    return [
        f"Voice Delivery ({PHASE})",
        "  Mode: voice-delivery-planner | read_only=True | can_act=False | tts_call=False",
        (
            "  Full/private dispatch: "
            f"strategy={config.get('mode')} | lead={config.get('lead_segments')} | "
            f"lead_min={config.get('lead_min_chars')} | lead_max={config.get('lead_max_chars')} | "
            f"tail={'full' if config.get('tail_full') else 'budgeted'}"
        ),
        (
            "  Runtime: "
            f"voice_status={runtime_state} | provider={voice_snap.get('provider', 'none')} | "
            f"ready={voice_snap.get('ready', False)} | queue={queue_size}/{queue_max} | "
            f"dropped={runtime.get('dropped_total', 0)} | last_error={shorten_line(runtime.get('last_error') or 'none', 70)}"
        ),
        (
            "  Last plan: "
            f"strategy={last.get('strategy', 'none')} | chunks={len(last.get('chunks') or [])} | "
            f"lead={last.get('lead_count', 0)} | tail={last.get('tail_count', 0)} | "
            f"chars={last.get('voice_chars', 0)}/{last.get('original_chars', 0)} | "
            f"warnings={warnings}"
        ),
        (
            "  Budget link: "
            f"last={budget_last.get('voice_chars', 0)}/{budget_last.get('original_chars', 0)} | "
            f"reason={budget_last.get('reason', 'none')} | command=/voice-budget-status"
        ),
        (
            "  Stats: "
            f"built={stats.get('built', 0)} | lead_then_tail={stats.get('lead_then_tail', 0)} | "
            f"tail_full={stats.get('tail_full', 0)} | warnings={stats.get('warnings', 0)}"
        ),
        "  Commands: /voice-delivery-status | /voice-delivery-preview <text> | /voice-delivery-preview story|<text>",
        "  Safety: preview/status only | no LLM | no TTS/VTS/OBS/Discord/game input | no memory write",
    ]


def _preview_payload(payload: str) -> tuple[str, str]:
    text = str(payload or "").strip()
    if "|" in text:
        maybe_mode, rest = text.split("|", 1)
        mode = maybe_mode.strip().lower()
        if mode in {"full", "story", "chat", "manual", "casual"}:
            return mode, rest.strip()
    return "story", text


def voice_delivery_preview_lines(payload: str, voice: Any = None) -> list[str]:
    mode, text = _preview_payload(payload)
    if not text:
        return ["  Usage: /voice-delivery-preview <text> | /voice-delivery-preview story|<text>"]
    plan = build_voice_delivery_plan(text, mode=mode, voice=voice)
    warnings = ",".join(plan.warnings) or "none"
    lines = [
        f"Voice Delivery Preview ({PHASE})",
        f"  Mode: {plan.mode} | strategy={plan.strategy} | queue_state={plan.queue_state}",
        (
            "  Packet plan: "
            f"chunks={len(plan.chunks)} | lead={plan.lead_count} | tail={plan.tail_count} | "
            f"voice_chars={plan.voice_chars}/{plan.original_chars} | omitted={plan.omitted_chars} | "
            f"warnings={warnings}"
        ),
    ]
    for chunk in plan.chunks:
        lines.append(
            "  "
            f"#{chunk.index} {chunk.role} | chars={chunk.chars} | "
            f"reason={chunk.reason} | text={shorten_line(chunk.text, 120)}"
        )
    lines.extend(
        [
            "  Dispatch rule: lead packets are small sentence packets; tail keeps the remaining text intact.",
            "  Safety: preview only | no LLM | no TTS call | no write | no output action",
        ]
    )
    return lines
