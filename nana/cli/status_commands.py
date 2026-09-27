"""Lazy status command router for Nana CLI.

This keeps the large core.status compatibility module off the normal chat
dispatcher hot path.  Status functions are imported only after a matching
read-only command is received.
"""

from __future__ import annotations

from importlib import import_module


_STATUS_MODULE_BY_NAME = {
    "print_attention_state": "nana.core.status_runtime",
    "print_recovery_summary": "nana.core.status_runtime",
    "print_time_state": "nana.core.status_runtime",
    "print_nana_status": "nana.core.status_runtime",
    "print_runtime_status": "nana.core.status_runtime",
    "print_stage_status": "nana.core.status_stage",
    "print_llm_route_status": "nana.core.status_public",
    "print_llm_route_probe": "nana.core.status_public",
    "print_llm_route_bakeoff": "nana.core.status_public",
    "print_persona_boundary_test": "nana.core.status_public",
    "print_viewer_chat_status": "nana.core.status_public",
    "print_social_session_status": "nana.core.status_public",
    "print_memory_grounding_status": "nana.core.status_public",
    "print_social_starter_status": "nana.core.status_public",
    "print_social_starter_preview": "nana.core.status_public",
    "print_public_quality_status": "nana.core.status_public",
    "print_public_quality_preview": "nana.core.status_public",
    "print_public_stage_status": "nana.core.status_public",
    "print_public_stage_preview": "nana.core.status_public",
    "print_viewer_chat_clear": "nana.core.status_public",
    "print_viewer_chat_test": "nana.core.status_public",
    "print_external_bridge_status": "nana.core.status_public",
    "print_external_bridge_test": "nana.core.status_public",
    "print_stream_ready_status": "nana.core.status_stream",
    "print_stream_event_log": "nana.core.status_stream",
}


def _status_fn(name: str):
    module_name = _STATUS_MODULE_BY_NAME[name]
    return getattr(import_module(module_name), name)


def _print_basic_status() -> None:
    from nana.runtime.browser_refresh import print_browser_state
    from nana.runtime.context import context_lock, context_state, get_confidence

    with context_lock:
        zone = context_state["active_zone"]
        app = context_state["active_app"]
        idle = context_state["idle_state"]
        flow = context_state["in_flow"]
    confidence = get_confidence()
    print(f"📊 Zone={zone} | App={app} | Idle={idle} | Flow={flow} | Confidence={confidence:.2f}")
    _status_fn("print_time_state")()
    print_browser_state()
    _status_fn("print_attention_state")()
    _status_fn("print_recovery_summary")()


def handle_status_command(loop, voice, text: str, text_lower: str | None = None) -> bool:
    """Handle read-only status/probe commands without top-level core.status import."""

    text_lower = text_lower or text.lower()

    if text_lower == "/status":
        _print_basic_status()
        return True

    if text_lower in {"/stage-status", "/stage"}:
        _status_fn("print_stage_status")(voice)
        return True

    if text_lower in {"/stream-ready-status", "/stream-readiness-status", "/stream-preflight-status"}:
        _status_fn("print_stream_ready_status")(voice)
        return True

    if text_lower in {"/stream-event-log", "/stream-event-timeline", "/stream-timeline"}:
        _status_fn("print_stream_event_log")()
        return True

    if text_lower in {"/llm-route-status", "/model-route-status"}:
        _status_fn("print_llm_route_status")()
        return True

    if text_lower == "/llm-route-probe" or text_lower == "/model-route-probe":
        _status_fn("print_llm_route_probe")("")
        return True

    if text_lower.startswith("/llm-route-probe ") or text_lower.startswith("/model-route-probe "):
        _, _, args = text.partition(" ")
        _status_fn("print_llm_route_probe")(args.strip())
        return True

    if text_lower == "/llm-route-bakeoff" or text_lower == "/model-route-bakeoff":
        _status_fn("print_llm_route_bakeoff")("")
        return True

    if text_lower.startswith("/llm-route-bakeoff ") or text_lower.startswith("/model-route-bakeoff "):
        _, _, args = text.partition(" ")
        _status_fn("print_llm_route_bakeoff")(args.strip())
        return True

    if text_lower.startswith("/persona-boundary-test"):
        rest = text[len("/persona-boundary-test"):].strip()
        if rest:
            parts = [part.strip() for part in rest.split("|")]
            scope = parts[0] if len(parts) > 0 and parts[0] else "public"
            name = parts[1] if len(parts) > 1 and parts[1] else "viewer"
            message = parts[2] if len(parts) > 2 and parts[2] else "hello"
        else:
            scope, name, message = "public", "viewer", "hello"
        _status_fn("print_persona_boundary_test")(scope, name, message)
        return True

    if text_lower in {"/viewer-chat-status", "/viewer-status", "/chat-viewer-status"}:
        _status_fn("print_viewer_chat_status")()
        return True

    if text_lower in {"/social-session-status", "/public-rhythm-status", "/viewer-rhythm-status"}:
        _status_fn("print_social_session_status")()
        return True

    if text_lower in {"/memory-grounding-status", "/memory-evidence-status", "/confidence-verifier-status"}:
        _status_fn("print_memory_grounding_status")()
        return True

    if text_lower in {"/social-starter-status", "/starter-status"}:
        _status_fn("print_social_starter_status")()
        return True

    if text_lower in {"/social-starter-preview", "/starter-preview"}:
        _status_fn("print_social_starter_preview")()
        return True

    if text_lower in {"/public-quality-status", "/quality-status"}:
        _status_fn("print_public_quality_status")()
        return True

    if text_lower in {"/public-stage-status", "/stage-identity-status"}:
        _status_fn("print_public_stage_status")()
        return True

    if text_lower.startswith("/public-stage-preview") or text_lower.startswith("/stage-identity-preview"):
        rest = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        viewer = "viewer"
        text_input = rest
        if "|" in rest:
            parts = [part.strip() for part in rest.split("|")]
            if len(parts) == 2:
                viewer, text_input = parts
            elif len(parts) >= 3:
                viewer = parts[0]
                text_input = "|".join(parts[1:])
        if not text_input:
            text_input = "Có gì cần hỗ trợ không bạn?"
        text_input = text_input.strip().strip("\"'`“”‘’")
        viewer = viewer.strip().strip("\"'`“”‘’") or "viewer"
        _status_fn("print_public_stage_preview")(text_input, viewer_name=viewer)
        return True

    if text_lower.startswith("/public-quality-preview") or text_lower.startswith("/quality-preview"):
        rest = text.split(maxsplit=1)[1].strip() if len(text.split(maxsplit=1)) > 1 else ""
        viewer = "viewer"
        vibe = "quiet_room"
        text_input = rest
        if "|" in rest:
            parts = [part.strip() for part in rest.split("|")]
            if len(parts) == 3:
                viewer, vibe, text_input = parts
            elif len(parts) == 2:
                viewer, text_input = parts
        if not text_input:
            text_input = "Chào viewer, có gì cần Nana giúp không?"
        _status_fn("print_public_quality_preview")(text_input, viewer=viewer, vibe=vibe)
        return True

    if text_lower in {"/viewer-chat-clear", "/viewer-clear"}:
        _status_fn("print_viewer_chat_clear")()
        return True

    if text_lower.startswith("/viewer-chat-test"):
        rest = text[len("/viewer-chat-test"):].strip()
        platform, channel, name, message = "discord", "chung", "viewer", "hello Nana"
        if rest:
            parts = [part.strip() for part in rest.split("|")]
            if len(parts) >= 4:
                platform = parts[0] or platform
                channel = parts[1] or channel
                name = parts[2] or name
                message = "|".join(parts[3:]).strip() or message
            elif len(parts) >= 2:
                name = parts[0] or name
                message = "|".join(parts[1:]).strip() or message
            elif parts:
                message = parts[0] or message
        _status_fn("print_viewer_chat_test")(name=name, message=message, platform=platform, channel=channel)
        return True

    if text_lower in {"/discord-core-status", "/external-bridge-status", "/bridge-core-status"}:
        _status_fn("print_external_bridge_status")()
        return True

    if text_lower in {"/presence-node-status", "/presence-session-status"}:
        from nana.runtime.presence_session_server import (
            print_presence_session_status,
        )

        print_presence_session_status()
        return True

    if text_lower in {
        "/presence-node-diagnostics",
        "/presence-diagnostics",
        "/presence-session-diagnostics",
    }:
        from nana.runtime.presence_session_server import (
            print_presence_session_diagnostics,
        )

        print_presence_session_diagnostics()
        return True

    if text_lower.startswith("/discord-core-test") or text_lower.startswith("/external-bridge-test"):
        prefix = "/discord-core-test" if text_lower.startswith("/discord-core-test") else "/external-bridge-test"
        rest = text[len(prefix):].strip()
        name, message = "linh", "hello Nana"
        if rest:
            parts = [part.strip() for part in rest.split("|")]
            if len(parts) >= 2:
                name = parts[0] or name
                message = "|".join(parts[1:]).strip() or message
            else:
                message = parts[0] or message
        _status_fn("print_external_bridge_test")(name=name, message=message)
        return True

    if text_lower in {"/nana-status", "/ns"}:
        _status_fn("print_nana_status")()
        return True

    if text_lower == "/runtime-status":
        _status_fn("print_runtime_status")()
        return True

    if text_lower in {"/reconcile", "/runtime-reconcile"}:
        from nana.runtime.reconcile import start_runtime_reconcile

        status = start_runtime_reconcile(loop)
        print("🧩 Background Reconcile")
        print(f"  Status: {status}")
        if status == "started":
            print("  Mode: background local_summary; prompt trả ngay, kết quả xem bằng /reconcile-status hoặc /runtime-status.")
        elif status == "already_running":
            print("  Mode: reuse running task; không tạo task nặng thứ hai.")
        else:
            print(f"  Reason: {status}")
        return True

    if text_lower in {"/reconcile-status", "/runtime-reconcile-status"}:
        from nana.runtime.reconcile import print_runtime_reconcile_status

        print_runtime_reconcile_status()
        return True

    return False
