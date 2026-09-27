"""Full stage readiness status panel for Nana."""

from __future__ import annotations

import os

from nana.config import BROWSER_FRESH_SECONDS
from nana.core.format import shorten_line
from nana.memory import memory, memory_lock
from nana.runtime.attention import context_for_attention, format_attention_status
from nana.runtime.browser_state import browser_age_seconds
from nana.runtime.context import context_snapshot, get_confidence
from nana.runtime.persona import evaluate_presence_rhythm

try:
    from nana.runtime import memory_grounding as _memory_grounding  # noqa: F401
    _GROUNDING_STATUS_AVAILABLE = True
except Exception:
    _GROUNDING_STATUS_AVAILABLE = False

def _active_memory_counts() -> tuple[int, int, int, int]:
    with memory_lock:
        long_term = list(memory.get("long_term", []))
        short_term = list(memory.get("short_term", []))
        chat_log = list(memory.get("chat_log", []))
        labels = dict(memory.get("memory_labels", {}))
    return len(long_term), len(short_term), len(chat_log), len(labels)

def _has_configured_secret(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"OPENAI_KEY_CUA_BAN", "ELEVENLABS_KEY_CUA_BAN", "YOUR_API_KEY", "YOUR_ELEVEN_API_KEY"})

def _voice_status_snapshot(voice=None) -> dict:
    from nana.config import (
        DEBUG_NO_TTS,
        ELEVEN_API_KEY,
        ELEVEN_OUTPUT_FORMAT,
        VOICE_CACHE_DIR,
        VOICE_CACHE_ENABLED,
        VOICE_CHUNKING_ENABLED,
        VOICE_CHUNK_MAX_CHARS,
        VOICE_ID,
        VOICE_STREAMING_DIRECT_ONLY,
        VOICE_STREAMING_DRY_RUN_ENABLED,
        VOICE_STREAMING_ENABLED,
        VOICE_STREAMING_KILL_SWITCH,
        VOICE_STREAMING_PILOT_ENABLED,
        VOICE_TEST_MODE,
    )

    configured = _has_configured_secret(ELEVEN_API_KEY) and bool(str(VOICE_ID or "").strip())
    disabled = bool(DEBUG_NO_TTS or VOICE_TEST_MODE)
    ready = bool(configured and not disabled)
    mode = "ready_no_call" if ready else ("configured_disabled" if configured else "unconfigured")
    runtime = {}
    runtime_available = False
    if voice is not None:
        snap_fn = getattr(voice, "snapshot", None)
        if callable(snap_fn):
            try:
                runtime = dict(snap_fn())
                runtime_available = True
            except Exception as exc:
                runtime = {"last_error": repr(exc)}

    return {
        "provider": "elevenlabs" if _has_configured_secret(ELEVEN_API_KEY) else "none",
        "configured": configured,
        "ready": ready,
        "mode": mode,
        "disabled": disabled,
        "debug_no_tts": bool(DEBUG_NO_TTS),
        "test_mode": bool(VOICE_TEST_MODE),
        "voice_id_present": bool(str(VOICE_ID or "").strip()),
        "output_format": ELEVEN_OUTPUT_FORMAT,
        "cache_enabled": bool(VOICE_CACHE_ENABLED),
        "cache_dir": str(VOICE_CACHE_DIR),
        "chunking_enabled": bool(VOICE_CHUNKING_ENABLED),
        "chunk_max_chars": VOICE_CHUNK_MAX_CHARS,
        "streaming_enabled": bool(VOICE_STREAMING_ENABLED),
        "streaming_dry_run_enabled": bool(VOICE_STREAMING_DRY_RUN_ENABLED),
        "streaming_pilot_enabled": bool(VOICE_STREAMING_PILOT_ENABLED),
        "streaming_kill_switch": bool(VOICE_STREAMING_KILL_SWITCH),
        "streaming_direct_only": bool(VOICE_STREAMING_DIRECT_ONLY),
        "runtime_available": runtime_available,
        "runtime": runtime,
        "voice_call": False,
    }

def _subtitle_status_snapshot() -> dict:
    try:
        from pathlib import Path
        from nana.runtime.stream_public_output import read_public_subtitle_status

        status = dict(read_public_subtitle_status())
        path = Path(str(status.get("path") or ""))
        parent = path.parent
        try:
            parent_exists = parent.exists()
            parent_writable = parent_exists and parent.is_dir()
        except Exception:
            parent_exists = False
            parent_writable = False
        status.update(
            {
                "ready": bool(parent_exists and parent_writable),
                "parent": str(parent),
                "parent_exists": bool(parent_exists),
                "parent_writable": bool(parent_writable),
                "obs_api_call": False,
            }
        )
        return status
    except Exception as exc:
        return {
            "decision": "public_subtitle_status_unavailable",
            "path": "",
            "exists": False,
            "bytes": None,
            "age_ms": None,
            "fresh": False,
            "stale_threshold_ms": 10000,
            "line": "",
            "ready": False,
            "parent": "",
            "parent_exists": False,
            "parent_writable": False,
            "file_write": False,
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "obs_api_call": False,
            "real_input": False,
            "submit": False,
            "error": repr(exc),
        }

def print_stage_status(voice=None) -> None:
    """Read-only stage readiness panel for the new Nana runtime."""

    snapshot = context_snapshot()
    browser = dict(snapshot.get("browser", {}))
    proactive = dict(snapshot.get("proactive", {}))
    age = browser_age_seconds(browser)
    browser_fresh = bool(browser.get("available")) and age is not None and age <= BROWSER_FRESH_SECONDS
    long_count, short_count, chat_count, label_count = _active_memory_counts()
    attention = format_attention_status(context_for_attention(snapshot))
    presence = evaluate_presence_rhythm(
        {
            "zone": snapshot.get("active_zone"),
            "active_app": snapshot.get("active_app"),
            "idle_state": snapshot.get("idle_state"),
            "idle_seconds": snapshot.get("idle_seconds"),
            "in_flow": snapshot.get("in_flow"),
            "last_chat_age": None,
        },
        commit=False,
    )

    try:
        from nana.integrations.vts import get_vts_runtime, vts_snapshot
        vts = vts_snapshot(get_vts_runtime())
    except Exception as exc:
        vts = {
            "ready": False,
            "connected": False,
            "last_error": repr(exc),
            "auth_status_label": "unknown",
            "auth_status": None,
        }

    try:
        from nana.runtime.expression_router import get_expression_router
        router = get_expression_router().get_status()
    except Exception as exc:
        router = {
            "enabled": False,
            "vts_available": False,
            "vts_connected": False,
            "cooldown_remaining_seconds": 0.0,
            "missing_count_total": 0,
            "last_missing_expression": repr(exc),
            "catalog_size": 0,
        }

    voice_status = _voice_status_snapshot(voice)
    voice_runtime = dict(voice_status.get("runtime") or {})
    subtitle_status = _subtitle_status_snapshot()
    try:
        from nana.runtime.viewer_chat import get_viewer_chat_queue
        viewer_chat = get_viewer_chat_queue().snapshot()
    except Exception as exc:
        viewer_chat = {
            "queued": 0,
            "max_messages": 0,
            "bridge_connected": False,
            "public_persona": "unknown",
            "error": repr(exc),
        }
    try:
        from nana.runtime.external_bridge import get_external_bridge_runtime
        external_bridge = get_external_bridge_runtime().snapshot()
    except Exception as exc:
        external_bridge = {
            "enabled": False,
            "running": False,
            "pending": 0,
            "stats": {},
            "error": repr(exc),
        }
    try:
        from nana.runtime.social_session import get_social_session
        social_session = get_social_session().snapshot()
    except Exception as exc:
        social_session = {
            "viewer_count": 0,
            "chat_velocity_per_second": 0.0,
            "last_decision": None,
            "stats": {},
            "error": repr(exc),
        }
    try:
        from nana.runtime.social_starters import get_social_starter
        social_starter = get_social_starter().snapshot()
    except Exception as exc:
        social_starter = {
            "is_blocked": True,
            "blocked_reason": repr(exc),
            "starter_count": 0,
            "last_starter_preview": "",
            "safety": {"auto_send": False, "preview_only": True},
        }

    try:
        from nana.cli.autonomy_command import autonomy_status_snapshot
        autonomy = autonomy_status_snapshot()
    except Exception as exc:
        autonomy = {"state": "unknown", "error": repr(exc)}

    core_ready = True
    awareness_state = "ready" if browser_fresh else ("standby_no_browser" if not browser.get("available") else "stale")
    vts_state = "ready" if vts.get("ready") else "soft_off"
    router_state = "ready" if router.get("enabled") else "disabled"
    presence_state = presence.get("rhythm") or "unknown"
    autonomy_state = autonomy.get("state") or "unknown"

    print("🎬 Stage Status")
    print("  Entry: python -m nana")
    print("  Architecture: new runtime core | legacy main proxy removed")
    print(
        "  Core chat: "
        f"{'ready' if core_ready else 'not_ready'} | "
        f"zone={snapshot.get('active_zone')} | app={snapshot.get('active_app')} | "
        f"flow={snapshot.get('in_flow')} | confidence={get_confidence():.2f}"
    )
    print(
        "  Awareness: "
        f"{awareness_state} | browser_available={browser.get('available')} | "
        f"fresh={browser_fresh} | kind={browser.get('kind')} | "
        f"age={'None' if age is None else f'{age:.1f}s'}"
    )
    print(f"  Browser title: {shorten_line(browser.get('title'), 100)}")
    if len(attention) >= 4:
        print(f"  Attention: {attention[1].strip()} | {attention[2].strip()} | {attention[3].strip()}")
    print(
        "  Presence: "
        f"enabled={presence.get('enabled', True)} | rhythm={presence_state} | "
        f"reason={presence.get('last_reason')} | proactive={proactive.get('enabled', True)}"
    )
    print(
        "  Memory: "
        f"long={long_count}/50 | short={short_count}/16 | chat={chat_count}/50 | labels={label_count}"
    )
    print(
        "  Memory grounding: "
        f"enabled={_GROUNDING_STATUS_AVAILABLE} | verifier=heuristic | command=/memory-grounding-status"
    )
    try:
        from nana.runtime.llm_route_status import llm_route_snapshot

        route = llm_route_snapshot()
        print(
            "  LLM route: "
            f"public={route.get('public_primary')} | core={route.get('core_primary')} | "
            f"cheap={route.get('cheap_primary')} | command=/llm-route-status"
        )
    except Exception as exc:
        print(f"  LLM route: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.core_self import get_core_self

        core_self = get_core_self().snapshot()
        stats = core_self.get("stats") or {}
        print(
            "  Core self: "
            f"phase={core_self.get('phase')} | checks={stats.get('checks')} | "
            f"service_tool={stats.get('service_tool_hits')} | command=/core-self-status | inject=pre-spine"
        )
    except Exception as exc:
        print(f"  Core self: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.core_drift_monitor import get_core_drift_monitor

        core_drift = get_core_drift_monitor().snapshot()
        drift_stats = dict(core_drift.get("stats") or {})
        last_drift = dict(core_drift.get("last_report") or {})
        print(
            "  Core drift: "
            f"grade={last_drift.get('grade', 'none')} | "
            f"drift={last_drift.get('drift_score', 0.0)} | "
            f"critical={drift_stats.get('critical_events', 0)} | "
            f"warnings={drift_stats.get('warning_events', 0)} | "
            "command=/core-drift-status"
        )
    except Exception as exc:
        print(f"  Core drift: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.core_anchor_recovery import get_core_anchor_recovery

        core_anchor = get_core_anchor_recovery().snapshot()
        anchor_stats = dict(core_anchor.get("stats") or {})
        last_anchor = dict(core_anchor.get("last_directive") or {})
        print(
            "  Core anchor recovery: "
            f"mode={last_anchor.get('mode', 'none')} | "
            f"trigger={last_anchor.get('trigger', 'none')} | "
            f"active={anchor_stats.get('active', 0)} | "
            f"command=/core-anchor-status"
        )
    except Exception as exc:
        print(f"  Core anchor recovery: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_voice_style import get_public_voice_style

        voice_style = get_public_voice_style().snapshot()
        voice_stats = voice_style.get("stats") or {}
        print(
            "  Public voice style: "
            f"phase={voice_style.get('phase')} | checks={voice_stats.get('checks')} | "
            f"soft_service={voice_stats.get('service_soft_hits')} | teeth={voice_stats.get('teeth_hits')} | "
            "command=/public-voice-status | inject=public-pre-boundary"
        )
    except Exception as exc:
        print(f"  Public voice style: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_conversation_director import get_public_conversation_director

        conversation = get_public_conversation_director().snapshot()
        last_conversation = dict(conversation.get("last_directive") or {})
        print(
            "  Public conversation director: "
            f"thread={last_conversation.get('thread')} | "
            f"intent={last_conversation.get('viewer_intent')} | "
            f"move={last_conversation.get('response_move')} | "
            f"confidence={last_conversation.get('confidence')} | "
            "command=/public-conversation-status"
        )
    except Exception as exc:
        print(f"  Public conversation director: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_thread_memory import get_public_thread_memory

        thread_memory = get_public_thread_memory().snapshot()
        last_thread_memory = dict(thread_memory.get("last_directive") or {})
        print(
            "  Public thread memory: "
            f"thread={last_thread_memory.get('active_thread')} | "
            f"intent={last_thread_memory.get('current_intent')} | "
            f"memory={last_thread_memory.get('response_memory')} | "
            f"repeat={last_thread_memory.get('same_prompt_count')} | "
            "command=/public-thread-memory-status"
        )
    except Exception as exc:
        print(f"  Public thread memory: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_running_jokes import get_public_running_jokes

        running_jokes = get_public_running_jokes().snapshot()
        last_joke = dict(running_jokes.get("last_detected_directive") or running_jokes.get("last_directive") or {})
        print(
            "  Public running jokes: "
            f"joke={last_joke.get('joke_key')} | "
            f"count={last_joke.get('count')} | "
            f"mode={last_joke.get('callback_mode')} | "
            f"confidence={last_joke.get('confidence')} | "
            "command=/public-joke-bank-status"
        )
    except Exception as exc:
        print(f"  Public running jokes: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_viewer_memory import get_public_viewer_memory

        viewer_memory = get_public_viewer_memory().snapshot()
        last_viewer_memory = dict(viewer_memory.get("last_directive") or {})
        print(
            "  Public viewer memory: "
            f"viewer={last_viewer_memory.get('viewer_name', 'none')} | "
            f"profile={last_viewer_memory.get('viewer_profile', 'none')} | "
            f"pattern={last_viewer_memory.get('session_pattern', 'none')} | "
            f"topic={last_viewer_memory.get('current_topic', 'none')} | "
            "command=/public-viewer-memory-status"
        )
    except Exception as exc:
        print(f"  Public viewer memory: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_scene_builder import get_public_scene_builder

        scene_builder = get_public_scene_builder().snapshot()
        last_scene = dict(scene_builder.get("last_directive") or {})
        print(
            "  Public scene builder: "
            f"scene={last_scene.get('scene_type', 'none')} | "
            f"move={last_scene.get('scene_move', 'none')} | "
            f"topic={last_scene.get('topic', 'none')} | "
            f"confidence={last_scene.get('confidence', 0.0)} | "
            "command=/public-scene-status"
        )
    except Exception as exc:
        print(f"  Public scene builder: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_quiet_room_rhythm import get_public_quiet_room_rhythm

        quiet_room = get_public_quiet_room_rhythm().snapshot()
        quiet_stats = dict(quiet_room.get("stats") or {})
        last_quiet = dict(quiet_room.get("last_directive") or {})
        print(
            "  Public quiet-room rhythm: "
            f"mode={last_quiet.get('mode', 'none')} | "
            f"repeat={last_quiet.get('repeat_count', 0)} | "
            f"first={quiet_stats.get('first_scene', 0)} | "
            f"fresh={quiet_stats.get('fresh_scene', 0)} | "
            "command=/public-quiet-room-status"
        )
    except Exception as exc:
        print(f"  Public quiet-room rhythm: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_fluency_polish import get_public_fluency_polisher

        fluency = get_public_fluency_polisher().snapshot()
        fluency_stats = dict(fluency.get("stats") or {})
        last_fluency = dict(fluency.get("last_result") or {})
        print(
            "  Public fluency polish: "
            f"changed={last_fluency.get('changed', False)} | "
            f"checked={fluency_stats.get('checked', 0)} | "
            f"fixed={fluency_stats.get('changed', 0)} | "
            "command=/public-fluency-status"
        )
    except Exception as exc:
        print(f"  Public fluency polish: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_reply_evaluator import get_public_reply_evaluator

        reply_eval = get_public_reply_evaluator().snapshot()
        reply_eval_stats = dict(reply_eval.get("stats") or {})
        last_reply_eval = dict(reply_eval.get("last_result") or {})
        issue_kinds = last_reply_eval.get("issue_kinds") or []
        print(
            "  Public reply evaluator: "
            f"grade={last_reply_eval.get('grade', 'none')} | "
            f"score={last_reply_eval.get('score', 0.0)} | "
            f"issues={len(issue_kinds)} | "
            f"checked={reply_eval_stats.get('checked', 0)} | "
            "command=/public-reply-eval-status"
        )
    except Exception as exc:
        print(f"  Public reply evaluator: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_reply_feedback import get_public_reply_feedback

        reply_feedback = get_public_reply_feedback().snapshot()
        feedback_stats = dict(reply_feedback.get("stats") or {})
        last_feedback = dict(reply_feedback.get("last_directive") or {})
        print(
            "  Public reply feedback: "
            f"mode={last_feedback.get('mode', 'none')} | "
            f"trigger={last_feedback.get('trigger', 'none')} | "
            f"active={feedback_stats.get('active', 0)} | "
            "command=/public-reply-feedback-status"
        )
    except Exception as exc:
        print(f"  Public reply feedback: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_fallback_recovery import get_public_fallback_recovery

        recovery = get_public_fallback_recovery().snapshot()
        recovery_stats = dict(recovery.get("stats") or {})
        last_recovery = dict(recovery.get("last_result") or {})
        print(
            "  Public fallback recovery: "
            f"theme={last_recovery.get('theme', 'none')} | "
            f"recovered={recovery_stats.get('recovered', 0)} | "
            f"mode={recovery.get('mode')} | "
            "command=/public-fallback-recovery-status"
        )
    except Exception as exc:
        print(f"  Public fallback recovery: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_memory_filter import get_public_memory_filter

        memory_filter = get_public_memory_filter().snapshot()
        filter_stats = dict(memory_filter.get("stats") or {})
        last_filter = dict(memory_filter.get("last_directive") or {})
        print(
            "  Public memory filter: "
            f"kind={last_filter.get('kind', 'none')} | "
            f"eligible={last_filter.get('learning_eligible', 'none')} | "
            f"weight={last_filter.get('learning_weight', 0.0)} | "
            f"excluded={filter_stats.get('excluded', 0)} | "
            "command=/public-memory-filter-status"
        )
    except Exception as exc:
        print(f"  Public memory filter: error={type(exc).__name__}: {exc}")
    print("  Persona spine: enabled=True | command=/persona-spine-status | inject=pre-boundary")
    try:
        from nana.runtime.mood_continuity import get_mood_continuity

        mood = get_mood_continuity().snapshot()
        social_temp = dict(mood.social_temperature or {})
        print(
            "  Mood continuity: "
            f"internal={mood.mood} | energy={mood.energy:.2f} | focus={mood.focus:.2f} | "
            f"tension={mood.tension:.2f} | social={social_temp.get('state', 'unknown')} "
            f"({social_temp.get('trend', 'unknown')}) | command=/mood-status | inject=pre-boundary"
        )
    except Exception as exc:
        print(f"  Mood continuity: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.intention_planner import get_intention_planner

        intention_snap = get_intention_planner().snapshot()
        intention_last = dict(intention_snap.get("last_plan") or {})
        print(
            "  Intention planner: "
            f"active={intention_last.get('category')}:{intention_last.get('intention')} | "
            f"tone={intention_last.get('tone')} | "
            f"priority={intention_last.get('priority')} | rank={intention_last.get('priority_rank')} | "
            f"command=/intention-status"
        )
    except Exception as exc:
        print(f"  Intention planner: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.session_review_adapter import session_review_snapshot

        review = session_review_snapshot()
        print(
            "  Session review adapter: "
            f"replies={review.get('reply_events')}/{review.get('reply_files_scanned')} | "
            f"outbox={review.get('outbox_events')}/{review.get('outbox_files_scanned')} | "
            f"corrupt={review.get('corrupt_files')} | "
            "mode=metrics-only | command=/session-review-status"
        )
    except Exception as exc:
        print(f"  Session review adapter: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.post_stream_review import post_stream_review_snapshot

        post_review = post_stream_review_snapshot()
        print(
            "  Post-stream review: "
            f"recommendations={len(post_review.get('recommendations') or [])} | "
            f"reason={post_review.get('reason')} | "
            "mode=recommendation-only | command=/post-stream-review"
        )
    except Exception as exc:
        print(f"  Post-stream review: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.post_stream_lessons import lesson_snapshot

        lesson_snap = lesson_snapshot()
        print(
            "  Post-stream lessons: "
            f"pending={lesson_snap.get('recommendation_count')} | "
            f"active={len(lesson_snap.get('active_lessons') or [])} | "
            f"revoked={len(lesson_snap.get('revoked_lessons') or [])} | "
            f"rejected={len(lesson_snap.get('dismissed') or [])} | "
            "mode=owner-approved | command=/post-stream-lessons-status"
        )
    except Exception as exc:
        print(f"  Post-stream lessons: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.lane_leak_audit import lane_leak_snapshot

        lane_audit = lane_leak_snapshot()
        print(
            "  Lane leak audit: "
            f"passed={lane_audit.get('passed')} | failed={lane_audit.get('failed')} | "
            f"critical={lane_audit.get('critical')} | command=/lane-leak-audit"
        )
    except Exception as exc:
        print(f"  Lane leak audit: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.memory_consolidation_preview import memory_consolidation_snapshot

        mem_audit = memory_consolidation_snapshot()
        categories = mem_audit.get("categories") or {}
        print(
            "  Memory consolidation: "
            f"long={mem_audit.get('long_count')}/{mem_audit.get('long_limit')} | "
            f"duplicates={len(mem_audit.get('duplicate_groups') or [])} | "
            f"review={categories.get('review', 0)} | stale={categories.get('stale-like', 0)} | "
            "mode=preview-only | command=/memory-consolidation-preview"
        )
    except Exception as exc:
        print(f"  Memory consolidation: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.context_budget_audit import context_budget_snapshot

        budget = context_budget_snapshot("public_stage")
        print(
            "  Context budget audit: "
            f"tokens≈{budget.get('total_tokens_est')} | warnings={budget.get('warnings')} | "
            "mode=estimate-only | command=/context-budget-audit"
        )
    except Exception as exc:
        print(f"  Context budget audit: error={type(exc).__name__}: {exc}")
    print("  Lane affect: enabled=True | command=/lane-affect-status | public_affection=clamped")
    stream_policy_for_stage = None
    try:
        from nana.runtime.stream_signal_refresh import refresh_stream_signals
        from nana.runtime.stream_state import get_stream_state

        refresh_stream_signals(voice=voice, reason="stage_status")
        stream_status_for_stage = get_stream_state().status()
        stream_policy_for_stage = get_stream_state().get_policy()
        stream_policy_data = stream_policy_for_stage.to_dict()
        print(
            "  Stream state: "
            f"state={stream_policy_data.get('state')} | "
            f"lifecycle={stream_status_for_stage.get('lifecycle', 'unknown')} | "
            f"proactive={stream_policy_data.get('can_proactive')} | "
            f"auto_send={stream_policy_data.get('can_auto_send')} | "
            f"tone={stream_policy_data.get('interaction_tone')} | "
            f"avatar_energy={stream_policy_data.get('avatar_energy')} | "
            f"reply={stream_policy_data.get('can_reply')} | "
            f"speak={stream_policy_data.get('can_speak')} | "
            f"avatar={stream_policy_data.get('can_avatar')}"
        )
    except Exception as exc:
        print(f"  Stream state: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.stream_ready_status import stream_ready_snapshot

        stream_ready = stream_ready_snapshot(voice)
        live_blockers = ", ".join(stream_ready.get("live_blockers") or []) or "none"
        warnings = ", ".join(stream_ready.get("warnings") or []) or "none"
        print(
            "  Stream ready: "
            f"rehearsal={stream_ready.get('rehearsal_ready')} | "
            f"live={stream_ready.get('live_ready')} | "
            f"blockers={shorten_line(live_blockers, 80)} | "
            f"warnings={shorten_line(warnings, 80)} | command=/stream-ready-status"
        )
    except Exception as exc:
        print(f"  Stream ready: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.stream_event_timeline import stream_event_timeline_snapshot

        timeline = stream_event_timeline_snapshot(limit=5)
        last_timeline = dict(timeline.get("last_event") or {})
        last_timeline_text = (
            f"{last_timeline.get('kind')}:{last_timeline.get('age_seconds', 0.0):.1f}s"
            if last_timeline
            else "none"
        )
        print(
            "  Stream timeline: "
            f"events={timeline.get('count', 0)} | "
            f"last={last_timeline_text} | command=/stream-event-log"
        )
    except Exception as exc:
        print(f"  Stream timeline: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.proactive_engine import get_proactive_engine

        proactive_preview = get_proactive_engine().should_proactive(
            policy=stream_policy_for_stage,
            chat_velocity=0.0,
            last_proactive_age_s=999.0,
            last_nana_chat_age_s=999.0,
        )
        print(
            "  Proactive stage: "
            f"can={proactive_preview.can_proactive} | "
            f"should={proactive_preview.should_proactive} | "
            f"reason={shorten_line(proactive_preview.reason, 80)} | "
            f"tone={proactive_preview.suggested_tone}"
        )
    except Exception as exc:
        print(f"  Proactive stage: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.avatar_event_bridge import get_avatar_event_bridge

        avatar_event_snapshot = get_avatar_event_bridge().snapshot()
        avatar_event_last = avatar_event_snapshot.get("last_event", {})
        avatar_stage_event = avatar_event_last.get("event") or "message"
        print(
            "  Avatar event: "
            f"event={avatar_stage_event} | "
            f"source={avatar_event_last.get('source') or 'none'} | "
            f"viewer={avatar_event_last.get('viewer_name') or 'none'} | "
            f"age={avatar_event_last.get('age_seconds', 0.0):.1f}s"
        )
    except Exception as exc:
        avatar_stage_event = "message"
        print(f"  Avatar event: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.avatar_director import get_avatar_director

        avatar_preview = get_avatar_director().get_directive(
            policy=stream_policy_for_stage,
            chat_event=avatar_stage_event,
        )
        print(
            "  Avatar director: "
            f"can={avatar_preview.can_avatar} | "
            f"expression={avatar_preview.expression} | "
            f"body={avatar_preview.body_language} | "
            f"reason={shorten_line(avatar_preview.reason, 80)}"
        )
    except Exception as exc:
        print(f"  Avatar director: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.avatar_reactor import get_avatar_reaction_controller

        avatar_reaction_plan = get_avatar_reaction_controller().build_plan(
            chat_event=avatar_stage_event,
            dry_run=True,
        )
        print(
            "  Avatar reaction: "
            f"ok={avatar_reaction_plan.ok} | "
            f"expression={avatar_reaction_plan.expression} | "
            f"hotkey={avatar_reaction_plan.hotkey or 'none'} | "
            f"vts_call={avatar_reaction_plan.vts_call} | "
            f"reason={shorten_line(avatar_reaction_plan.reason, 80)}"
        )
    except Exception as exc:
        print(f"  Avatar reaction: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_avatar_reaction import get_public_avatar_reaction

        public_avatar = get_public_avatar_reaction().snapshot()
        public_avatar_last = dict(public_avatar.get("last") or {})
        public_avatar_plan = dict(public_avatar_last.get("plan") or {})
        print(
            "  Public avatar reaction: "
            f"intent={public_avatar_last.get('intent') or 'none'} | "
            f"event={public_avatar_last.get('event') or 'none'} | "
            f"expression={public_avatar_last.get('expression') or 'none'} | "
            f"plan_ok={public_avatar_plan.get('ok', 'none')} | "
            f"command=/public-avatar-status"
        )
    except Exception as exc:
        print(f"  Public avatar reaction: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.avatar_live_hook import get_avatar_live_hook

        avatar_live_hook = get_avatar_live_hook().snapshot()
        avatar_live_stats = avatar_live_hook.get("stats", {})
        avatar_live_last = avatar_live_hook.get("last_result", {})
        print(
            "  Avatar live hook: "
            f"ok={avatar_live_last.get('ok')} | "
            f"action={avatar_live_last.get('action')} | "
            f"event={avatar_live_last.get('event')} | "
            f"planned={avatar_live_stats.get('planned')} | "
            f"blocked={avatar_live_stats.get('blocked')} | "
            f"reason={shorten_line(avatar_live_last.get('reason'), 60)}"
        )
    except Exception as exc:
        print(f"  Avatar live hook: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.avatar_vts_dispatch import get_avatar_vts_dispatch_gate

        avatar_vts = get_avatar_vts_dispatch_gate().snapshot()
        avatar_vts_stats = avatar_vts.get("stats", {})
        avatar_vts_last = avatar_vts.get("last_result", {})
        print(
            "  Avatar VTS dispatch: "
            f"enabled={avatar_vts.get('enabled')} | "
            f"sent={avatar_vts_stats.get('sent')} | "
            f"blocked={avatar_vts_stats.get('blocked')} | "
            f"last={avatar_vts_last.get('action')}:{shorten_line(avatar_vts_last.get('reason'), 60)}"
        )
    except Exception as exc:
        print(f"  Avatar VTS dispatch: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.avatar_intent_gateway import get_avatar_intent_gateway

        avatar_runtime = get_avatar_intent_gateway().snapshot()
        avatar_runtime_arbiter = avatar_runtime.get("arbiter", {})
        print(
            "  Avatar intent runtime: "
            f"enabled={avatar_runtime.get('enabled')} | "
            f"running={avatar_runtime.get('running')} | "
            f"transport={avatar_runtime.get('transport')} | "
            f"active={len(avatar_runtime_arbiter.get('active', []))} | "
            f"pending={len(avatar_runtime_arbiter.get('pending', []))} | "
            f"command=/avatar-runtime-status"
        )
    except Exception as exc:
        print(f"  Avatar intent runtime: error={type(exc).__name__}: {exc}")
    print(
        "  Autonomy: "
        f"state={autonomy_state} | paused={autonomy.get('paused')} | "
        f"accepted={autonomy.get('accepted')} | rejected={autonomy.get('rejected')}"
    )
    print(
        "  VTS: "
        f"{vts_state} | connected={vts.get('connected')} | "
        f"auth={vts.get('auth_status_label')} ({vts.get('auth_status')})"
    )
    print(
        "  Voice: "
        f"ready={voice_status['ready']} | provider={voice_status['provider']} | "
        f"runtime={voice_status['runtime_available']} | "
        f"speaking={voice_runtime.get('speaking')} | queue="
        f"{voice_runtime.get('queue_size', 'n/a')}/{voice_runtime.get('queue_maxsize', 'n/a')}"
    )
    try:
        from nana.runtime.voice_reply_budget import get_voice_reply_budget

        voice_budget = get_voice_reply_budget().snapshot()
        voice_budget_stats = dict(voice_budget.get("stats") or {})
        voice_budget_last = dict(voice_budget.get("last_result") or {})
        print(
            "  Voice budget: "
            f"last={voice_budget_last.get('voice_chars', 0)}/{voice_budget_last.get('original_chars', 0)} | "
            f"truncated={voice_budget_stats.get('truncated', 0)} | "
            f"story_max={voice_budget.get('story_max_chars')} | command=/voice-budget-status"
        )
    except Exception as exc:
        print(f"  Voice budget: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.voice_delivery import get_voice_delivery

        voice_delivery = get_voice_delivery().snapshot()
        voice_delivery_stats = dict(voice_delivery.get("stats") or {})
        voice_delivery_last = dict(voice_delivery.get("last_plan") or {})
        print(
            "  Voice delivery: "
            f"strategy={voice_delivery_last.get('strategy', 'none')} | "
            f"chunks={len(voice_delivery_last.get('chunks') or [])} | "
            f"tail={voice_delivery_last.get('tail_count', 0)} | "
            f"plans={voice_delivery_stats.get('built', 0)} | command=/voice-delivery-status"
        )
    except Exception as exc:
        print(f"  Voice delivery: error={type(exc).__name__}: {exc}")
    print(
        "  Subtitle: "
        f"ready={subtitle_status.get('ready')} | exists={subtitle_status.get('exists')} | "
        f"fresh={subtitle_status.get('fresh')} | obs_api_call={subtitle_status.get('obs_api_call')} | "
        f"path={shorten_line(subtitle_status.get('path'), 90)}"
    )
    print(
        "  Viewer chat: "
        f"queued={viewer_chat.get('queued')}/{viewer_chat.get('max_messages')} | "
        f"bridge_connected={viewer_chat.get('bridge_connected')} | "
        f"persona={viewer_chat.get('public_persona')} | "
        f"priority_users={','.join(viewer_chat.get('priority_viewers') or []) or 'none'} | "
        "can_act=False"
    )
    external_stats = dict(external_bridge.get("stats") or {})
    print(
        "  External bridge: "
        f"enabled={external_bridge.get('enabled')} | running={external_bridge.get('running')} | "
        f"pending={external_bridge.get('pending')} | processed={external_stats.get('processed', 0)} | "
        f"reply_written={external_stats.get('reply_written', 0)} | text_only=True"
    )
    social_stats = dict(social_session.get("stats") or {})
    social_last = dict(social_session.get("last_decision") or {})
    print(
        "  Social session: "
        f"viewers={social_session.get('viewer_count')} | "
        f"velocity={social_session.get('chat_velocity_per_second')}/s | "
        f"last={social_last.get('action') or 'none'}:{social_last.get('reason') or 'none'} | "
        f"vibe={social_last.get('room_vibe') or 'none'} | "
        f"familiarity={social_last.get('familiarity') or 'none'} | "
        f"director={social_last.get('director_mode') or 'none'} | "
        f"full={social_stats.get('full_reply', 0)} | ack={social_stats.get('ack_only', 0)} | "
        f"skip={social_stats.get('skip', 0)}"
    )
    starter_blocked = bool(social_starter.get("is_blocked"))
    starter_reason = social_starter.get("blocked_reason") or ("blocked" if starter_blocked else "ready")
    print(
        "  Social starter: "
        f"eligible={not starter_blocked} | reason={starter_reason} | "
        f"topic={social_starter.get('room_topic') or 'none'} | "
        f"shown={social_starter.get('starter_count', 0)} | auto_send=False"
    )
    try:
        from nana.runtime.starter_send import get_starter_send_controller

        starter_send = get_starter_send_controller().snapshot()
        starter_send_stats = dict(starter_send.get("stats") or {})
        print(
            "  Starter send: "
            f"enabled={starter_send.get('enabled')} | "
            f"channel={starter_send.get('channel_id')} | "
            f"sent={starter_send_stats.get('sent', 0)} | "
            f"blocked={starter_send_stats.get('blocked', 0)} | "
            f"cooldown={starter_send.get('cooldown_remaining', 0):.0f}s | "
            "auto_send=False"
        )
    except Exception as exc:
        print(f"  Starter send: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.starter_auto import get_starter_auto_scheduler

        starter_auto = get_starter_auto_scheduler().snapshot()
        starter_auto_stats = dict(starter_auto.get("stats") or {})
        starter_auto_last = dict(starter_auto.get("last_result") or {})
        print(
            "  Starter auto: "
            f"enabled={starter_auto.get('enabled')} | "
            f"worker={starter_auto.get('worker_running')} | "
            f"sent={starter_auto_stats.get('sent', 0)} | "
            f"blocked={starter_auto_stats.get('blocked', 0)} | "
            f"cooldown={starter_auto.get('cooldown_remaining', 0):.0f}s | "
            f"last={starter_auto_last.get('action') or 'none'}:{starter_auto_last.get('reason') or 'none'}"
        )
    except Exception as exc:
        print(f"  Starter auto: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_quality import get_public_quality_guard
        quality_snap = get_public_quality_guard().snapshot()
        quality_stats = dict(quality_snap.get("stats") or {})
        total_cleaned = (
            int(quality_stats.get("greeting_stripped", 0))
            + int(quality_stats.get("service_bot_rewritten", 0))
            + int(quality_stats.get("tech_leak_blocked", 0))
            + int(quality_stats.get("ba_con_blocked", 0))
            + int(quality_stats.get("too_long_truncated", 0))
            + int(quality_stats.get("opener_replaced", 0))
            + int(quality_stats.get("fallback_used", 0))
        )
        last_action = (
            "fallback" if quality_stats.get("fallback_used")
            else (
                "truncated" if quality_stats.get("too_long_truncated")
                else (
                    "rewritten" if total_cleaned else "clean"
                )
            )
        )
        print(
            "  Public quality: "
            f"enabled={quality_snap.get('enabled')} | fixed={total_cleaned} | "
            f"clean={quality_stats.get('passed_clean', 0)} | last={last_action}"
        )
    except Exception as exc:
        print(f"  Public quality: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.public_stage_identity import get_public_stage_identity_guard
        stage_snap = get_public_stage_identity_guard().snapshot()
        stage_stats = dict(stage_snap.get("stats") or {})
        total_stage = (
            int(stage_stats.get("backstage_command_blocks", 0))
            + int(stage_stats.get("assistant_tone_rewrites", 0))
            + int(stage_stats.get("operator_tone_blocks", 0))
            + int(stage_stats.get("ba_con_blocks", 0))
        )
        last_stage = stage_snap.get("last_action") or "clean"
        print(
            "  Public stage identity: "
            f"enabled={stage_snap.get('enabled')} | "
            f"command_blocks={stage_stats.get('backstage_command_blocks', 0)} | "
            f"residue_replaced={stage_stats.get('residue_replaced', 0)} | "
            f"name_throttled={stage_stats.get('name_throttle_stripped', 0)} | "
            f"variety_swaps={stage_stats.get('variety_swaps', 0)} | "
            f"rewrites={stage_stats.get('assistant_tone_rewrites', 0)} | "
            f"last={last_stage}"
        )
    except Exception as exc:
        print(f"  Public stage identity: error={type(exc).__name__}: {exc}")
    print(
        "  Expression router: "
        f"{router_state} | vts_available={router.get('vts_available')} | "
        f"vts_connected={router.get('vts_connected')} | "
        f"cooldown={float(router.get('cooldown_remaining_seconds') or 0.0):.1f}s | "
        f"missing={router.get('missing_count_total')} | catalog={router.get('catalog_size')}"
    )
    print("  Safety: read_only_status=True | can_act=False | game_input=False | obs_call=False")
    print("  Live verify: /status | /stage-status | /vts-status | /awareness-status | /memory-status")
