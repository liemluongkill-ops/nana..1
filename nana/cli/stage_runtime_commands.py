"""Stage runtime, avatar, mood, and planner command router.

The handlers here preserve the old ``handle_text`` behavior while moving a
self-contained command cluster out of the main dispatcher.
"""

from __future__ import annotations

from collections.abc import Iterable

from nana.runtime.avatar_director import avatar_director_preview_lines, avatar_director_status
from nana.runtime.avatar_event_bridge import (
    avatar_event_preview_lines,
    avatar_event_status_lines,
    avatar_event_test_lines,
)
from nana.runtime.avatar_live_hook import (
    avatar_live_hook_plan_lines,
    avatar_live_hook_preview_lines,
    avatar_live_hook_status_lines,
)
from nana.runtime.avatar_reactor import (
    avatar_reaction_apply_preview_lines,
    avatar_reaction_preview_lines,
    avatar_reaction_status_lines,
)
from nana.runtime.avatar_vts_dispatch import (
    avatar_vts_disable_lines,
    avatar_vts_dispatch_lines,
    avatar_vts_enable_lines,
    avatar_vts_preview_lines,
    avatar_vts_status_lines,
)
from nana.runtime.avatar_intent_gateway import (
    avatar_runtime_look_preview_lines,
    avatar_runtime_look_submit_lines,
    avatar_runtime_preview_lines,
    avatar_runtime_start_lines,
    avatar_runtime_status_lines,
    avatar_runtime_stop_lines,
    avatar_runtime_submit_lines,
)
from nana.runtime.intention_planner import (
    intention_preview_lines,
    intention_refresh_lines,
    intention_status_lines,
)
from nana.runtime.mood_continuity import (
    mood_preview_lines,
    mood_reset_lines,
    mood_status_lines,
    mood_test_lines,
)
from nana.runtime.proactive_engine import proactive_decision_summary, proactive_preview_lines
from nana.runtime.public_avatar_reaction import (
    public_avatar_preview_lines,
    public_avatar_status_lines,
)
from nana.runtime.stream_signal_refresh import refresh_stream_signals
from nana.runtime.stream_state import get_stream_state, stream_state_status_lines


def _print_lines(lines: Iterable[str]) -> None:
    for line in lines:
        print(line)


def _arg_after_space(text: str, default: str = "") -> str:
    parts = text.split(maxsplit=1)
    return parts[1].strip() if len(parts) > 1 else default


def _starts_with_any(text_lower: str, prefixes: tuple[str, ...]) -> str:
    for prefix in prefixes:
        if text_lower.startswith(prefix):
            return prefix
    return ""


async def handle_stage_runtime_command(vts, text: str, text_lower: str | None = None) -> bool:
    """Handle stream/avatar/mood/planner command surfaces."""

    text_lower = text_lower or text.lower()

    if text_lower in {"/stream-status"}:
        refresh_stream_signals(reason="stream_status")
        _print_lines(stream_state_status_lines())
        return True

    if text_lower in {"/stream-policy-preview"}:
        refresh_stream_signals(reason="stream_policy_preview")
        policy = get_stream_state().get_policy()
        print("🎬 Stream Policy Preview (STAGE-8A)")
        for key, value in policy.to_dict().items():
            print(f"  {key}: {value}")
        print("  Safety: read_only=True | no chat send | no TTS/VTS/OBS/game input")
        return True

    if text_lower in {"/stream-live-on"}:
        policy = get_stream_state().go_live()
        print(f"  Stream: {policy.state.value} | {policy.reason}")
        return True

    if text_lower in {"/stream-live-off"}:
        policy = get_stream_state().end_stream()
        print(f"  Stream: {policy.state.value} | {policy.reason}")
        return True

    if text_lower in {"/stream-break"}:
        policy = get_stream_state().go_intermission()
        print(f"  Stream: {policy.state.value} | {policy.reason}")
        return True

    if text_lower in {"/stream-resume"}:
        policy = get_stream_state().resume_from_intermission()
        print(f"  Stream: {policy.state.value} | {policy.reason}")
        return True

    if text_lower in {"/proactive-stage-status", "/proactive-status"}:
        _print_lines(proactive_decision_summary())
        return True

    if text_lower in {"/proactive-stage-preview", "/proactive-preview"}:
        _print_lines(proactive_preview_lines())
        return True

    if text_lower in {"/avatar-director-status", "/avatar-stage-status"}:
        _print_lines(avatar_director_status())
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-director-preview", "/avatar-stage-preview"))
    if prefix:
        _print_lines(avatar_director_preview_lines(_arg_after_space(text, "message")))
        return True

    if text_lower in {"/avatar-reaction-status", "/avatar-reactor-status"}:
        _print_lines(avatar_reaction_status_lines())
        return True

    if text_lower in {"/avatar-runtime-status", "/avatar-gateway-status"}:
        _print_lines(avatar_runtime_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-runtime-look-preview", "/avatar-gateway-look-preview"))
    if prefix:
        _print_lines(avatar_runtime_look_preview_lines(_arg_after_space(text, "left")))
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-runtime-look", "/avatar-gateway-look"))
    if prefix:
        _print_lines(avatar_runtime_look_submit_lines(_arg_after_space(text, "left")))
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-runtime-preview", "/avatar-gateway-preview"))
    if prefix:
        _print_lines(avatar_runtime_preview_lines(_arg_after_space(text, "wave")))
        return True

    if text_lower in {"/avatar-runtime-start", "/avatar-gateway-start"}:
        _print_lines(avatar_runtime_start_lines())
        return True

    if text_lower in {"/avatar-runtime-stop", "/avatar-gateway-stop"}:
        _print_lines(avatar_runtime_stop_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-runtime-submit", "/avatar-gateway-submit"))
    if prefix:
        _print_lines(avatar_runtime_submit_lines(_arg_after_space(text, "wave")))
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-reaction-preview", "/avatar-reactor-preview"))
    if prefix:
        _print_lines(avatar_reaction_preview_lines(_arg_after_space(text, "message")))
        return True

    if text_lower in {"/public-avatar-status", "/avatar-public-status"}:
        _print_lines(public_avatar_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/public-avatar-preview", "/avatar-public-preview"))
    if prefix:
        _print_lines(public_avatar_preview_lines(_arg_after_space(text, "message")))
        return True

    prefix = _starts_with_any(
        text_lower,
        ("/avatar-reaction-apply-preview", "/avatar-reactor-apply-preview"),
    )
    if prefix:
        _print_lines(avatar_reaction_apply_preview_lines(_arg_after_space(text, "message")))
        return True

    if text_lower in {"/avatar-vts-status", "/avatar-dispatch-status"}:
        _print_lines(avatar_vts_status_lines())
        return True

    if text_lower in {"/avatar-vts-enable", "/avatar-dispatch-enable"}:
        _print_lines(avatar_vts_enable_lines())
        return True

    if text_lower in {"/avatar-vts-disable", "/avatar-dispatch-disable"}:
        _print_lines(avatar_vts_disable_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-vts-preview", "/avatar-dispatch-preview"))
    if prefix:
        _print_lines(avatar_vts_preview_lines(_arg_after_space(text, "message")))
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-vts-dispatch", "/avatar-dispatch"))
    if prefix:
        _print_lines(await avatar_vts_dispatch_lines(vts=vts, chat_event=_arg_after_space(text, "message")))
        return True

    if text_lower in {"/avatar-event-status", "/avatar-events-status"}:
        _print_lines(avatar_event_status_lines())
        return True

    if text_lower in {"/avatar-event-preview", "/avatar-events-preview"}:
        _print_lines(avatar_event_preview_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/avatar-event-test", "/avatar-events-test"))
    if prefix:
        _print_lines(avatar_event_test_lines(_arg_after_space(text, "")))
        return True

    if text_lower in {"/avatar-live-hook-status", "/avatar-hook-status"}:
        _print_lines(avatar_live_hook_status_lines())
        return True

    if text_lower in {"/avatar-live-hook-preview", "/avatar-hook-preview"}:
        _print_lines(avatar_live_hook_preview_lines())
        return True

    if text_lower in {"/avatar-live-hook-plan", "/avatar-hook-plan"}:
        _print_lines(avatar_live_hook_plan_lines())
        return True

    if text_lower in {"/mood-status", "/mood-continuity-status"}:
        _print_lines(mood_status_lines())
        return True

    prefix = _starts_with_any(text_lower, ("/mood-preview", "/mood-continuity-preview"))
    if prefix:
        lane = _arg_after_space(text, "") or None
        _print_lines(mood_preview_lines(lane))
        return True

    prefix = _starts_with_any(text_lower, ("/mood-test", "/mood-continuity-test"))
    if prefix:
        _print_lines(mood_test_lines(_arg_after_space(text, "")))
        return True

    if text_lower in {"/mood-reset", "/mood-continuity-reset"}:
        _print_lines(mood_reset_lines())
        return True

    if text_lower in {"/intention-status", "/planner-status"}:
        _print_lines(intention_status_lines())
        return True

    if text_lower in {"/intention-preview", "/planner-preview"}:
        _print_lines(intention_preview_lines())
        return True

    if text_lower in {"/intention-refresh", "/planner-refresh"}:
        _print_lines(intention_refresh_lines())
        return True

    return False
