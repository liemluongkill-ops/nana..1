"""Command route truth manifest.

This file is intentionally small and explicit.  It explains the confusing
middle ground between "registered for suggestions" and "handled live".
"""
from __future__ import annotations

from dataclasses import dataclass
import re

from nana.commands.dispatch_surface import dispatch_surface_status_lines, is_static_dispatch_command


RUNTIME_HOT_PATH = "__main__.py -> nana.cli.app -> nana.cli.handle_text"

LEGACY_PARKING_FILES = {
    "nana.main": "legacy parking lot; not imported by the hot path",
    "nana.main_cut": "dead cut artifact; not imported by the hot path",
    "nana.phases.phase_stubs": "legacy stub parking lot; current dispatcher does not rely on it",
    "nana.phases.phase6": "intentional stub until phase 6 is split out of the old main",
}


ARCHIVED_SLASH_COMMANDS = frozenset({
    "/phase5-status",
    "/phase6-status",
    "/phase6-ready",
    "/p6",
    "/p6-ready",
    "/phase21-1-status",
    "/phase21-2-status",
    "/phase21-3-status",
    "/phase21-4-status",
    "/phase21-1-ready",
    "/phase21-2-ready",
    "/phase21-3-ready",
    "/phase21-4-ready",
    "/p21-1",
    "/p21-2",
    "/p21-3",
    "/p21-4",
    "/p21-1-ready",
    "/p21-2-ready",
    "/p21-3-ready",
    "/p21-4-ready",
    "/phase22-1-ready",
    "/phase22-2-ready",
    "/phase22-3-ready",
    "/phase22-4-ready",
    "/p22-1-ready",
    "/p22-2-ready",
    "/p22-3-ready",
    "/p22-4-ready",
    "/phase23-1-ready",
    "/phase23-2-ready",
    "/phase23-3-ready",
    "/phase23-4-ready",
    "/p23-1-ready",
    "/p23-2-ready",
    "/p23-3-ready",
    "/p23-4-ready",
    "/phase24-1-ready",
    "/phase24-2-ready",
    "/phase24-3-ready",
    "/phase24-4-ready",
    "/p24-1-ready",
    "/p24-2-ready",
    "/p24-3-ready",
    "/p24-4-ready",
    "/phase25-1-ready",
    "/phase25-2-ready",
    "/p25-1-ready",
    "/p25-2-ready",
    "/live-voice-control-status",
    "/direct-voice-pilot-status",
    "/guarded-voice-dispatch-status",
    "/live-path-replacement-status",
    "/voice-telemetry-status",
    "/voice-streaming-decision-status",
    "/voice-streaming-dry-run-status",
    "/voice-stream-safety-status",
    "/voice-stream-gate-status",
    "/stream-pilot-control-status",
    "/guarded-stream-call-status",
    "/controlled-stream-pilot-status",
    "/stream-rollback-status",
    "/controlled-stream-gate-status",
    "/live-stream-measurement-status",
    "/stream-pilot-enable-status",
})


RESERVED_SLASH_COMMANDS = frozenset({
    "/adapter-auto",
    "/adapter-off",
    "/adapter-on",
    "/adapter-status",
    "/game-adapter-auto",
    "/game-adapter-off",
    "/game-adapter-on",
    "/game-adapter-status",
    "/game-status",
})


LIVE_SURFACE_HINTS = {
    "phase10_20": "phase 10-20 status/guard/test surfaces are routed to live phase modules",
    "public_stage": "STAGE-9 public/core surfaces are live diagnostic surfaces",
    "osu": "osu adapter commands are gated and dry/safety-first unless explicit executor gates are armed",
    "stardew": "Stardew commands are observer/planner bridge surfaces, not Python gameplay input",
}


@dataclass(frozen=True)
class CommandTruth:
    command: str
    status: str
    reason: str
    replacement: str = ""


def normalize_manifest_command(command: str) -> str:
    text = str(command or "").strip().split(maxsplit=1)[0].lower()
    if text.startswith("//"):
        text = "/" + text.lstrip("/")
    if text.startswith("/ "):
        text = "/" + text[2:].lstrip()
    return text


def classify_command_truth(command: str, *, known: bool = False) -> CommandTruth:
    cmd = normalize_manifest_command(command)
    if not cmd.startswith("/"):
        return CommandTruth(cmd, "chat", "not a slash command")

    if _is_archived_command(cmd):
        return CommandTruth(
            cmd,
            "archived",
            "fail-closed legacy surface; kept so old commands do not crash",
            "/status | /stage-status | /command-truth-status",
        )

    if cmd in RESERVED_SLASH_COMMANDS or _is_reserved_command(cmd):
        return CommandTruth(
            cmd,
            "reserved",
            "registered for compatibility or future router split, not proof of a live handler",
            "/help | /command-truth-status",
        )

    if is_static_dispatch_command(cmd):
        return CommandTruth(cmd, "live", "handler literal found in handle_text dispatcher")

    if _is_live_hint_command(cmd):
        return CommandTruth(cmd, "live", "known live diagnostic/runtime surface")

    if known:
        return CommandTruth(
            cmd,
            "known_registry",
            "known to the suggestion/normalization registry; handler truth still belongs to the dispatcher",
            "/command-truth <command>",
        )

    return CommandTruth(cmd, "unknown", "not in the known command registry")


def command_truth_lines(command: str, *, known: bool = False):
    truth = classify_command_truth(command, known=known)
    yield "Command Truth"
    yield f"  Command: {truth.command or 'none'}"
    yield f"  Status: {truth.status}"
    yield f"  Reason: {truth.reason}"
    if truth.replacement:
        yield f"  Replacement: {truth.replacement}"
    yield f"  Hot path: {RUNTIME_HOT_PATH}"
    yield "  Note: KNOWN_SLASH_COMMANDS is a compatibility/suggestion surface, not a guarantee of live handling."


def command_truth_status_lines(known_count: int = 0):
    yield "Command Router Truth"
    yield f"  Hot path: {RUNTIME_HOT_PATH}"
    yield f"  Registry size: {known_count} known slash strings"
    yield f"  Archived exact commands: {len(ARCHIVED_SLASH_COMMANDS)} + phase26-32 legacy status loop"
    yield f"  Reserved exact commands: {len(RESERVED_SLASH_COMMANDS)} + generated aspirational phase aliases"
    yield "  Rule: live command code lives in handle_text/router modules; registry membership alone is not truth."
    yield "  Check: /command-truth <command>"
    for line in dispatch_surface_status_lines():
        yield f"  {line}" if not line.startswith("  ") else f"  {line}"
    yield "  Legacy files:"
    for name, reason in sorted(LEGACY_PARKING_FILES.items()):
        yield f"    - {name}: {reason}"


def archived_handler_reason(name: str, phase: str | None = None):
    lowered = str(name or "").lower()
    if "phase5" in lowered or "phase6" in lowered:
        return (
            "phase 5/6 are still parked behind compatibility shells",
            "/stage-status | /phase7-status",
        )
    if phase and str(phase).isdigit() and 26 <= int(phase) <= 32:
        return (
            "phase 26-32 legacy foundation statuses are parked during router split",
            "/stage-status | /command-truth-status",
        )
    if any(part in lowered for part in ("stream", "voice", "phase21", "phase22", "phase23", "phase24", "phase25")):
        return (
            "old voice/stream lane is intentionally fail-closed until the stream pipeline is split safely",
            "/voice-status | /stream-status | /stage-status",
        )
    return (
        "archived compatibility shell; this command is not a live runtime surface",
        "/status | /stage-status",
    )


def _is_archived_command(cmd: str) -> bool:
    if cmd in ARCHIVED_SLASH_COMMANDS:
        return True
    if re.fullmatch(r"/phase(?:2[6-9]|3[0-2])(?:-[1-5])?-(?:status|ready|guard-status|test)", cmd):
        return True
    if re.fullmatch(r"/p(?:2[6-9]|3[0-2])(?:-[1-5])?(?:-ready)?", cmd):
        return True
    return False


def _is_reserved_command(cmd: str) -> bool:
    return bool(
        re.fullmatch(r"/p\d{3,}(?:-[a-z0-9-]+)?", cmd)
        or re.fullmatch(r"/phase5[0-9][a-z0-9-]*", cmd)
        or re.fullmatch(r"/phase\d{3,}[a-z0-9-]*", cmd)
    )


def _is_live_hint_command(cmd: str) -> bool:
    if cmd in {
        "/status",
        "/stage-status",
        "/command-truth-status",
        "/runtime-status",
        "/voice-status",
        "/stream-ready-status",
        "/stream-readiness-status",
        "/stream-preflight-status",
        "/stream-event-log",
        "/stream-event-timeline",
        "/stream-timeline",
        "/public-voice-status",
        "/core-self-status",
        "/core-drift-status",
        "/core-anchor-status",
    }:
        return True
    if re.fullmatch(r"/phase(?:1[0-9]|20)(?:-[1-9]|-10)?-(?:status|guard-status|test)", cmd):
        return True
    if cmd.startswith("/public-") or cmd.startswith("/core-") or cmd.startswith("/llm-route-"):
        return True
    if cmd.startswith("/osu"):
        return True
    if cmd.startswith("/stardew"):
        return True
    return False
