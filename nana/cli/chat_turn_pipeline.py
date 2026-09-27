"""Chat-turn pipeline for Nana CLI.

This module owns the non-command conversation path: persona observation,
cooldown, awareness context, LLM streaming, voice budgeting, emotion updates,
and memory writes. Slash/system command routing stays in handle_text.py.
"""

from __future__ import annotations

import asyncio
import time
import uuid

from nana.cli import globals as cli_globals
from nana.cli.globals import set_runtime_turn_state
from nana.config import (
    GPT_COOLDOWN,
    PRIVATE_VOICE_OVERLAP_COALESCE_MS,
    PRIVATE_VOICE_OVERLAP_ENABLED,
    PRIVATE_VOICE_OVERLAP_MAX_CHARS,
    PRIVATE_VOICE_OVERLAP_MIN_CHARS,
    PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS,
    PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS,
    PRIVATE_VOICE_TTD_ENABLED,
    PRIVATE_VOICE_TTD_MIN_CHARS,
    PRIVATE_VOICE_TTD_MIN_WORDS,
)
from nana.core.chat_surface import (
    awareness_memory_note_user_chat,
    build_casual_ping_reply,
    finalize_live_reply,
    is_casual_ping,
    is_story_request,
    recovery_notice,
)
from nana.core.diagnostics import is_diagnostic_fragment
from nana.memory import (
    extract_important,
    has_explicit_memory_write_intent,
    memory,
    memory_lock,
    save_chat_log,
    save_memory_async,
    update_emotion,
)
from nana.runtime.browser_refresh import ensure_browser_snapshot, refresh_browser_state
from nana.runtime.browser_state import current_browser_snapshot_state, is_browser_context_question
from nana.runtime.context import mark_chat_time
from nana.runtime.live_awareness import build_live_awareness_snapshot
from nana.runtime.logger import log_event
from nana.runtime.memory_grounding import is_memory_save_status_question
from nana.runtime.history_privacy import redact_history_text
from nana.runtime.reply_stream_privacy import ReplyStreamPrivacy
# Kept as a compatibility export for older smoke/diagnostic callers. Session
# continuity no longer invokes the pre-reply summary API.
from nana.runtime.memory_spine import get_memory_spine
from nana.runtime.persona import observe_text_for_persona
from nana.runtime.session_checkpoint import record_private_turn
from nana.runtime.private_voice_overlap import PrivateVoiceOverlapTurn
from nana.runtime.private_voice_ttd import PrivateVoiceTtdTurn
from nana.runtime.voice_delivery import build_voice_delivery_plan, should_flush_voice_buffer
from nana.runtime.voice_reply_budget import (
    get_voice_reply_budget,
    should_defer_stream_voice,
    voice_stream_dispatch_config,
    voice_budget_log_enabled,
)
from nana.brain.gpt import (
    StreamingReplySurfaceSanitizer,
    TerminalAudioTagStreamSanitizer,
    ask_gpt,
    ask_gpt_stream,
    strip_terminal_audio_tags,
)
from nana.integrations.vts import trigger_expression_lifecycle


PRIVATE_VOICE_POLICY = "full"
EXPLICIT_MEMORY_SAVE_FAILURE_REPLY = (
    "Lần này con chưa lưu được nội dung đó vào trí nhớ lâu dài. "
    "Ba thử lại sau nhé."
)
EXPLICIT_MEMORY_SAVE_SUCCESS_REPLY = "Con đã lưu thông tin đó vào trí nhớ lâu dài rồi Ba."
FAILED_MEMORY_SAVE_HISTORY_INPUT = (
    "USER: [explicit memory save failed; requested content was not retained]"
)


def _record_private_checkpoint(
    user_text: str,
    nana_text: str,
    event_id: str,
) -> None:
    """Best-effort continuity; a checkpoint failure cannot break the reply."""
    try:
        if has_explicit_memory_write_intent(str(user_text or "").lower()):
            return
        with memory_lock:
            record_private_turn(
                memory,
                user_text=user_text,
                nana_text=nana_text,
                event_id=event_id,
            )
    except Exception as exc:
        log_event("errors", f"Private session checkpoint failed: {type(exc).__name__}")


def _private_voice_policy_for(reply_mode: str | None = None) -> str:
    """Return the local/private TTS policy independently of reply semantics."""
    return PRIVATE_VOICE_POLICY


def _history_reply_from_stream(text: str) -> str:
    """Project emitted text for history; never rewrite speech after dispatch."""
    return ' '.join(redact_history_text(strip_terminal_audio_tags(text)).split())


def say_with_voice_budget(
    voice,
    say_text,
    *,
    mode: str | None = None,
    story_mode: bool = False,
    casual_mode: bool = False,
    note: bool = True,
):
    if mode is None:
        mode = "story" if story_mode else "casual" if casual_mode else "chat"
    result = get_voice_reply_budget().prepare(say_text, mode=mode)
    if result.voice_text:
        try:
            voice.say(result.voice_text, voice_mode=result.mode)
        except TypeError:
            voice.say(result.voice_text)
    if result.changed and note and voice_budget_log_enabled():
        print(
            "🎙️ Voice budget: "
            f"đọc {result.voice_chars}/{result.original_chars} ký tự; phần còn lại giữ ở text."
        )
    return result


async def handle_chat_turn(vts, voice, text: str, loop, text_lower: str | None = None) -> bool:
    turn_started_at = time.perf_counter()
    original_text = str(text or '')
    text = redact_history_text(original_text)
    text_lower = text.lower() if text != original_text else text_lower or text.lower()
    story_mode = is_story_request(text_lower)
    casual_mode = is_casual_ping(text_lower)
    reply_mode = "story" if story_mode else "casual" if casual_mode else "chat"
    voice_policy = _private_voice_policy_for(reply_mode)
    avatar_reply = None
    avatar_turn = None

    def _say_with_voice_budget(say_text, *, mode: str | None = None, note: bool = True):
        result = say_with_voice_budget(
            voice,
            say_text,
            mode=mode or voice_policy,
            story_mode=story_mode,
            casual_mode=casual_mode,
            note=note,
        )
        if avatar_turn is not None and result.voice_text:
            avatar_turn.consider(avatar_reply if avatar_reply is not None else say_text)
        return result

    if text_lower == "tắt ai":
        cli_globals.ai_active = False
        print("💤 Nana đã ngủ")
        return False

    if text_lower == "mở ai":
        cli_globals.ai_active = True
        print("🟢 Nana đã thức")
        return False

    if not cli_globals.ai_active:
        print("💤 Nana đang ngủ...")
        return False

    if is_diagnostic_fragment(text):
        print("🧾 Diagnostic fragment: ignored")
        print("  Note: dòng này giống output runtime, không gọi model và không lưu memory.")
        return False

    # Only the private-owner chat path can turn a direct pose request into a command.
    from nana.cli.avatar_requests import handle_owner_avatar_turn

    if await handle_owner_avatar_turn(text, _say_with_voice_budget):
        return False

    observe_text_for_persona(text)

    now = time.time()
    if now - cli_globals.last_gpt_time < GPT_COOLDOWN:
        print("⏳ Nana đang nghỉ tí...")
        return False

    cli_globals.last_gpt_time = now
    from nana.runtime.avatar_reply_turn import AvatarReplyTurn
    avatar_turn = AvatarReplyTurn()

    awareness_question = is_browser_context_question(text_lower)
    if awareness_question:
        await ensure_browser_snapshot(loop, reason="chat_live_awareness_cache")
        if current_browser_snapshot_state() != "FRESH":
            await refresh_browser_state(
                loop,
                show=False,
                reason="chat_live_awareness_refresh",
                force=True,
            )

    # A durable fact points to this private input event, not to an output ACK.
    private_event_id = "private-turn-" + uuid.uuid4().hex
    explicit_memory_write = has_explicit_memory_write_intent(original_text.lower())
    stored_item = extract_important(original_text, source_event_id=private_event_id, source="private")
    update_emotion(text)

    set_runtime_turn_state("thinking", "chat_response", "chat")
    if explicit_memory_write and stored_item is None:
        # A generated reply cannot turn a failed write into a durable receipt.
        # Keep the rejected content out of persisted conversation history too.
        reply = EXPLICIT_MEMORY_SAVE_FAILURE_REPLY
        print("🤖 Nana:", reply)
        _say_with_voice_budget(reply)
        update_emotion(text, reply)
        save_chat_log(FAILED_MEMORY_SAVE_HISTORY_INPUT)
        save_chat_log(f"NANA: {reply}")
        with memory_lock:
            memory["chat_log"].append(FAILED_MEMORY_SAVE_HISTORY_INPUT)
            memory["chat_log"].append(f"NANA: {reply}")
            memory["chat_log"] = memory["chat_log"][-50:]
            memory["short_term"].append(
                "ba: [explicit memory save failed; requested content was not retained]"
            )
            memory["short_term"].append(f"nana: {reply}")
            memory["short_term"] = memory["short_term"][-16:]
            memory["emotion"]["annoyance"] *= 0.9
            memory["emotion"]["playfulness"] *= 0.95
        mark_chat_time()
        asyncio.create_task(
            trigger_expression_lifecycle(
                vts,
                reply,
                voice=voice,
                reason="memory_save_failed",
                avatar_turn=avatar_turn,
            )
        )
        set_runtime_turn_state("idle", "memory_save_failed", "chat")
        awareness_memory_note_user_chat(
            user_text="[explicit memory save failed; content not retained]",
            nana_text=reply,
        )
        save_memory_async()
        return False

    verified_save_confirmation = (
        explicit_memory_write and stored_item is not None and is_memory_save_status_question(text)
    )
    if casual_mode or verified_save_confirmation:
        reply = (EXPLICIT_MEMORY_SAVE_SUCCESS_REPLY if verified_save_confirmation
                 else build_casual_ping_reply(text_lower))
        print("🤖 Nana:", reply)
        _say_with_voice_budget(reply)
        update_emotion(text, reply)
        save_chat_log(f"USER: {text}")
        save_chat_log(f"NANA: {reply}")
        with memory_lock:
            memory["chat_log"].append(f"USER: {text}")
            memory["chat_log"].append(f"NANA: {reply}")
            memory["chat_log"] = memory["chat_log"][-50:]
            memory["short_term"].append(f"ba: {text}")
            memory["short_term"].append(f"nana: {reply}")
            memory["short_term"] = memory["short_term"][-16:]
            memory["emotion"]["annoyance"] *= 0.9
            memory["emotion"]["playfulness"] *= 0.95
        mark_chat_time()
        asyncio.create_task(trigger_expression_lifecycle(vts, reply, voice=voice, reason="chat_reply", avatar_turn=avatar_turn))
        set_runtime_turn_state("idle", "reply_queued", "chat")
        awareness_memory_note_user_chat(user_text=text, nana_text=reply)
        _record_private_checkpoint(text, reply, private_event_id)
        save_memory_async()
        return False
    else:
        full_reply_parts = []
        voice_buf = ""
        printed_prefix = False
        reply_stream = StreamingReplySurfaceSanitizer()
        privacy_stream = ReplyStreamPrivacy()
        terminal_stream = TerminalAudioTagStreamSanitizer()
        full_voice_dispatch = voice_stream_dispatch_config(voice_policy)
        voice_budget = get_voice_reply_budget().begin_turn(
            mode=voice_policy,
            truncate=not bool(full_voice_dispatch.get("tail_full")),
        )
        defer_voice_stream = should_defer_stream_voice(voice_policy)
        voice_packets = 0
        VOICE_FLUSH_CHARS = 120 if story_mode else 60
        ttd_turn = None
        ttd_finish = None
        overlap_turn = None
        overlap_finish = None

        def commit_ttd_request(ttd_request):
            ticket = voice.say_ttd(
                ttd_request,
                voice_mode=voice_policy,
            )
            if ticket:
                avatar_turn.consider("".join(full_reply_parts))
            return ticket

        def commit_overlap_request(overlap_request):
            ticket = voice.say_overlap(
                overlap_request,
                voice_mode=voice_policy,
            )
            if ticket:
                avatar_turn.consider(overlap_request.lead_text)
            return ticket

        if PRIVATE_VOICE_TTD_ENABLED:
            readiness_fn = getattr(voice, "private_ttd_readiness", None)
            record_bypass = getattr(voice, "record_private_ttd_bypass", None)
            if not defer_voice_stream:
                if callable(record_bypass):
                    record_bypass("legacy_lead_packets_enabled")
            elif not callable(readiness_fn):
                if callable(record_bypass):
                    record_bypass("voice_ttd_api_missing")
            else:
                ready, reason = readiness_fn()
                if ready:
                    ttd_turn = PrivateVoiceTtdTurn(
                        commit_ttd_request,
                        minimum_chars=PRIVATE_VOICE_TTD_MIN_CHARS,
                        minimum_words=PRIVATE_VOICE_TTD_MIN_WORDS,
                        chunk_target_chars=PRIVATE_VOICE_TTD_CHUNK_TARGET_CHARS,
                        chunk_max_chars=PRIVATE_VOICE_TTD_CHUNK_MAX_CHARS,
                        turn_started_at=turn_started_at,
                    )
                elif callable(record_bypass):
                    record_bypass(reason)

        if ttd_turn is None and PRIVATE_VOICE_OVERLAP_ENABLED:
            readiness_fn = getattr(voice, "private_overlap_readiness", None)
            record_bypass = getattr(voice, "record_private_overlap_bypass", None)
            if not defer_voice_stream:
                if callable(record_bypass):
                    record_bypass("legacy_lead_packets_enabled")
            elif not callable(readiness_fn):
                if callable(record_bypass):
                    record_bypass("voice_overlap_api_missing")
            else:
                ready, reason = readiness_fn()
                if ready:
                    overlap_turn = PrivateVoiceOverlapTurn(
                        commit_overlap_request,
                        minimum_chars=PRIVATE_VOICE_OVERLAP_MIN_CHARS,
                        maximum_chars=PRIVATE_VOICE_OVERLAP_MAX_CHARS,
                        coalesce_ms=PRIVATE_VOICE_OVERLAP_COALESCE_MS,
                        turn_started_at=turn_started_at,
                    )
                elif callable(record_bypass):
                    record_bypass(reason)

        def queue_voice_text(voice_text: str):
            try:
                voice.say(voice_text, voice_mode=voice_policy)
            except TypeError:
                voice.say(voice_text)
            avatar_turn.consider(voice_text)

        if awareness_question:
            awareness = build_live_awareness_snapshot()
            checkpoint_eligible = True
            try:
                reply = ask_gpt(text, story_mode=story_mode, casual_mode=casual_mode)
                avatar_reply = reply
                reply = finalize_live_reply(
                    reply,
                    user_text=text,
                    story_mode=story_mode,
                    casual_mode=casual_mode,
                    awareness=awareness,
                )
            except Exception as exc:
                log_event("errors", f"GPT awareness reply failed: {exc}")
                checkpoint_eligible = False
                avatar_reply = None
                reply = recovery_notice("gpt_reply_failed", detail=exc, cooldown=False) or "API đang lỗi rồi Ba. Thử lại sau một chút nhé."
            if not str(reply or "").strip():
                checkpoint_eligible = False
                reply = "Con đang bắt lại ngữ cảnh cho Ba."
            reply = redact_history_text(reply)
            print("🤖 Nana:", reply)
            _say_with_voice_budget(reply)
            update_emotion(text, reply)
            save_chat_log(f"USER: {text}")
            save_chat_log(f"NANA: {reply}")
            with memory_lock:
                memory["chat_log"].append(f"USER: {text}")
                memory["chat_log"].append(f"NANA: {reply}")
                memory["chat_log"] = memory["chat_log"][-50:]
                memory["short_term"].append(f"ba: {text}")
                memory["short_term"].append(f"nana: {reply}")
                memory["short_term"] = memory["short_term"][-16:]
                memory["emotion"]["annoyance"] *= 0.9
                memory["emotion"]["playfulness"] *= 0.95
            mark_chat_time()
            asyncio.create_task(trigger_expression_lifecycle(vts, reply, voice=voice, reason="chat_reply", avatar_reply=avatar_reply, avatar_turn=avatar_turn))
            set_runtime_turn_state("idle", "reply_queued", "chat")
            awareness_memory_note_user_chat(user_text=text, nana_text=reply, awareness=awareness)
            if checkpoint_eligible:
                _record_private_checkpoint(text, reply, private_event_id)
            save_memory_async()
            return False

        def flush_voice(force=False, sentence_boundary=False):
            nonlocal voice_packets, voice_buf
            text_to_say = voice_buf.strip()
            if not text_to_say:
                return
            if defer_voice_stream:
                return
            if should_flush_voice_buffer(
                text_to_say,
                mode=voice_policy,
                packets_sent=voice_packets,
                dispatch_config=full_voice_dispatch,
                force=force,
                sentence_boundary=sentence_boundary,
                normal_flush_chars=VOICE_FLUSH_CHARS,
            ):
                voice_text = voice_budget.feed(text_to_say)
                if voice_text:
                    queue_voice_text(voice_text)
                    voice_packets += 1
                voice_buf = ""

        # Task 7C Fix: capture awareness BEFORE streaming, so the chat moment
        # gets proper browser context (app/zone/conf/focus) instead of empty.
        stream_awareness = None
        if not awareness_question:
            # Only build if we didn't already build above (avoid double build)
            try:
                stream_awareness = build_live_awareness_snapshot()
            except Exception:
                pass

        try:
            async for chunk in ask_gpt_stream(text, story_mode=story_mode, casual_mode=casual_mode):
                cleaned_chunk = reply_stream.feed(privacy_stream.feed(chunk))
                for ch in cleaned_chunk:
                    visible = terminal_stream.feed(ch)
                    if not printed_prefix:
                        visible = visible.lstrip()
                    if visible:
                        if not printed_prefix:
                            print("🤖 Nana:", visible, end="", flush=True)
                            printed_prefix = True
                        else:
                            print(visible, end="", flush=True)
                    voice_buf += ch
                    full_reply_parts.append(ch)
                    if ttd_turn is not None:
                        ttd_turn.feed(ch)
                    if overlap_turn is not None:
                        overlap_turn.feed(ch)
                    if ch in ".!?,;" and len(voice_buf) >= 30:
                        flush_voice(force=False, sentence_boundary=ch in ".!?。！？")
                    if ch == "]" and len(voice_buf) >= 15:
                        flush_voice(force=False, sentence_boundary=False)
            trailing_chunk = reply_stream.feed(privacy_stream.flush()) + reply_stream.flush()
            for ch in trailing_chunk:
                visible = terminal_stream.feed(ch)
                if not printed_prefix:
                    visible = visible.lstrip()
                if visible:
                    if not printed_prefix:
                        print("🤖 Nana:", visible, end="", flush=True)
                        printed_prefix = True
                    else:
                        print(visible, end="", flush=True)
                voice_buf += ch
                full_reply_parts.append(ch)
                if ttd_turn is not None:
                    ttd_turn.feed(ch)
                if overlap_turn is not None:
                    overlap_turn.feed(ch)
                if ch in ".!?,;" and len(voice_buf) >= 30:
                    flush_voice(force=False, sentence_boundary=ch in ".!?。！？")
                if ch == "]" and len(voice_buf) >= 15:
                    flush_voice(force=False, sentence_boundary=False)
            trailing_visible = terminal_stream.flush()
            if trailing_visible:
                if not printed_prefix:
                    trailing_visible = trailing_visible.lstrip()
                    if not trailing_visible:
                        trailing_visible = ""
                if trailing_visible and not printed_prefix:
                    print("🤖 Nana:", trailing_visible, end="", flush=True)
                    printed_prefix = True
                elif trailing_visible:
                    print(trailing_visible, end="", flush=True)
            if printed_prefix:
                print()
            stream_reply = "".join(full_reply_parts)
            if ttd_turn is not None:
                ttd_finish = ttd_turn.finish(stream_reply)
                if not ttd_finish.committed:
                    record_bypass = getattr(
                        voice,
                        "record_private_ttd_bypass",
                        None,
                    )
                    if callable(record_bypass):
                        record_bypass(ttd_finish.reason)
            if overlap_turn is not None:
                overlap_finish = overlap_turn.finish(stream_reply)
                if not overlap_finish.committed:
                    record_bypass = getattr(
                        voice,
                        "record_private_overlap_bypass",
                        None,
                    )
                    if callable(record_bypass):
                        record_bypass(overlap_finish.reason)
            if defer_voice_stream:
                voice_text = voice_budget.feed(stream_reply)
                if voice_text:
                    streaming_committed = bool(
                        (ttd_finish is not None and ttd_finish.committed)
                        or (overlap_finish is not None and overlap_finish.committed)
                    )
                    if streaming_committed:
                        if voice_text != stream_reply:
                            log_event(
                                "voice",
                                "Private streaming voice budget mismatch after commit; "
                                "keeping committed exact stream text",
                            )
                        voice_packets += 1
                    else:
                        queue_voice_text(voice_text)
                        voice_packets += 1
                voice_buf = ""
            else:
                flush_voice(force=True)
            voice_result = voice_budget.finalize()
            try:
                build_voice_delivery_plan(stream_reply, mode=voice_policy, voice=voice)
            except Exception as exc:
                log_event("voice", f"Voice delivery plan record failed: {exc}")
            if voice_result.changed and voice_budget_log_enabled():
                print(
                    "🎙️ Voice budget: "
                    f"đọc {voice_result.voice_chars}/{voice_result.original_chars} ký tự; phần còn lại giữ ở text."
                )
        except Exception as exc:
            ttd_committed = (
                ttd_turn.abort("llm_stream_failed")
                if ttd_turn is not None
                else False
            )
            overlap_committed = (
                overlap_turn.abort("llm_stream_failed")
                if overlap_turn is not None
                else False
            )
            voice_budget.finalize()
            log_event("errors", f"GPT stream failed: {redact_history_text(str(exc))}")
            fallback = redact_history_text(recovery_notice("gpt_reply_failed", detail=redact_history_text(str(exc)), cooldown=False) or "API đang lỗi rồi Ba. Thử lại sau một chút nhé.")
            recovery_spoken = not ttd_committed and not overlap_committed
            print("🤖 Nana:" if recovery_spoken else "⚠️", fallback)
            if not ttd_committed and not overlap_committed:
                _say_with_voice_budget(fallback)
            history_reply = fallback
            if full_reply_parts:
                history_reply = '[interrupted] ' + _history_reply_from_stream(''.join(full_reply_parts))
                if recovery_spoken:
                    history_reply += ' [recovery] ' + fallback
            update_emotion(text, fallback)
            save_chat_log(f"USER: {text}")
            save_chat_log(f"NANA: {history_reply}")
            with memory_lock:
                memory["chat_log"].append(f"USER: {text}")
                memory["chat_log"].append(f"NANA: {history_reply}")
                memory["chat_log"] = memory["chat_log"][-50:]
                memory["short_term"].append(f"ba: {text}")
                memory["short_term"].append(f"nana: {history_reply}")
                memory["short_term"] = memory["short_term"][-16:]
            mark_chat_time()
            asyncio.create_task(trigger_expression_lifecycle(vts, fallback, voice=voice, reason="chat_reply", avatar_turn=avatar_turn))
            set_runtime_turn_state("idle", "reply_queued", "chat")
            awareness_memory_note_user_chat(user_text=text, nana_text=history_reply, awareness=stream_awareness)
            save_memory_async()
            return False

        if not full_reply_parts:
            log_event("errors", "GPT stream returned no chunks; falling back to non-stream chat reply")
            checkpoint_eligible = True
            try:
                reply = ask_gpt(text, story_mode=story_mode, casual_mode=casual_mode)
                avatar_reply = reply
                reply = finalize_live_reply(
                    reply,
                    user_text=text,
                    story_mode=story_mode,
                    casual_mode=casual_mode,
                )
            except Exception as exc:
                log_event("errors", f"GPT fallback after empty stream failed: {exc}")
                checkpoint_eligible = False
                avatar_reply = None
                reply = recovery_notice("gpt_reply_failed", detail=exc, cooldown=False) or "API đang lỗi rồi Ba. Thử lại sau một chút nhé."
            if not str(reply or "").strip():
                checkpoint_eligible = False
                reply = "Con nghe nè Ba."
            reply = redact_history_text(reply)
            print("🤖 Nana:", reply)
            _say_with_voice_budget(reply)
            update_emotion(text, reply)
            save_chat_log(f"USER: {text}")
            save_chat_log(f"NANA: {reply}")
            with memory_lock:
                memory["chat_log"].append(f"USER: {text}")
                memory["chat_log"].append(f"NANA: {reply}")
                memory["chat_log"] = memory["chat_log"][-50:]
                memory["short_term"].append(f"ba: {text}")
                memory["short_term"].append(f"nana: {reply}")
                memory["short_term"] = memory["short_term"][-16:]
                memory["emotion"]["annoyance"] *= 0.9
                memory["emotion"]["playfulness"] *= 0.95
            mark_chat_time()
            asyncio.create_task(trigger_expression_lifecycle(vts, reply, voice=voice, reason="chat_reply", avatar_reply=avatar_reply, avatar_turn=avatar_turn))
            set_runtime_turn_state("idle", "reply_queued", "chat")
            awareness_memory_note_user_chat(user_text=text, nana_text=reply, awareness=stream_awareness)
            if checkpoint_eligible:
                _record_private_checkpoint(text, reply, private_event_id)
            save_memory_async()
            return False

        # The stream has already been printed and dispatched to full/overlap/TTD.
        # Keep it immutable: late finalization used to rename characters and
        # truncate the stored history while voice had received the complete text.
        avatar_reply = "".join(full_reply_parts)
        reply = _history_reply_from_stream(avatar_reply)

        update_emotion(text, reply)
        save_chat_log(f"USER: {text}")
        save_chat_log(f"NANA: {reply}")

        with memory_lock:
            memory["chat_log"].append(f"USER: {text}")
            memory["chat_log"].append(f"NANA: {reply}")
            memory["chat_log"] = memory["chat_log"][-50:]
            memory["short_term"].append(f"ba: {text}")
            memory["short_term"].append(f"nana: {reply}")
            memory["short_term"] = memory["short_term"][-16:]
            memory["emotion"]["annoyance"] *= 0.9
            memory["emotion"]["playfulness"] *= 0.95

        mark_chat_time()
        asyncio.create_task(trigger_expression_lifecycle(vts, reply, voice=voice, reason="chat_reply", avatar_reply=avatar_reply, avatar_turn=avatar_turn))
        set_runtime_turn_state("idle", "reply_queued", "chat")
        awareness_memory_note_user_chat(user_text=text, nana_text=reply, awareness=stream_awareness)
        _record_private_checkpoint(text, reply, private_event_id)
        save_memory_async()
        return False
