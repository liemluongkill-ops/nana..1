"""Nana handle_text - xu1t ly1 user input."""
import time

# === Shared mutable state ===
from nana.cli.globals import (
    LAST_USER_INPUT_DEDUP_WINDOW,
    autonomy_note_user_command,
    AUTONOMY_LOOP,
)

from nana.cli.input_guard import check_duplicate_user_input
from nana.cli.admin_command import handle_admin
from nana.cli.action_diagnostic_commands import handle_action_diagnostic_command
from nana.cli.adapter_commands import handle_adapter_command, handle_stardew_adapter_command
from nana.cli.awareness_commands import handle_awareness_command
from nana.cli.autonomy_runtime_commands import handle_autonomy_runtime_command
from nana.cli.identity_command import handle_identity_command
from nana.cli.core_identity_commands import handle_core_identity_command
from nana.cli.core_phase_commands import handle_core_phase_command
from nana.cli.memory_commands import handle_memory_command
from nana.cli.persona_commands import handle_persona_command
from nana.cli.phase_compat_commands import handle_phase_compat_command
from nana.cli.presence_commands import handle_presence_command
from nana.cli.process_commands import handle_process_command
from nana.cli.public_stage_commands import handle_public_stage_command
from nana.cli.review_audit_commands import handle_review_audit_command
from nana.cli.starter_commands import handle_starter_command
from nana.cli.stage_runtime_commands import handle_stage_runtime_command
from nana.cli.status_commands import handle_status_command
from nana.cli.vts_commands import handle_vts_command
from nana.cli.voice_commands import handle_voice_command
from nana.commands.help import print_command_help

# === Core utilities ===
from nana.commands.router import normalize_command_text, suggest_slash_command
from nana.runtime.priority_queue import runtime_queue


async def handle_text(vts, voice, text, loop):
    text = normalize_command_text(text)
    if not text:
        return False

    text_lower = text.lower()
    runtime_queue.mark_user_input()

    # Phase A6: chat input counts as a user override. The user just
    # explicitly engaged with Nana, so the next autonomy tick should
    # be allowed to talk back immediately, bypassing the user_is_typing
    # soft penalty. The override is one-shot; only the next tick uses it.
    if not text.startswith("/"):
        try:
            AUTONOMY_LOOP.request_user_override()
        except Exception:
            pass

    # Phase A6: /say <text> — force the next autonomy tick to speak,
    # bypassing all gates (including user_is_typing). Useful when the
    # user is actively typing and wants Nana to talk RIGHT NOW.
    if text.startswith("/say ") or text.startswith("/Say "):
        say_text = text[5:].strip()
        if not say_text:
            print("⚠️ Thiếu nội dung sau /say. Ví dụ: /say Con nhớ Ba ghê")
            return False
        try:
            AUTONOMY_LOOP.request_user_override()
        except Exception as exc:
            print(f"  [autonomy] /say override failed: {exc}")
        try:
            from nana.cli.chat_turn_pipeline import say_with_voice_budget

            say_with_voice_budget(voice, say_text, mode="manual")
        except Exception as exc:
            print(f"  [voice] /say TTS failed: {exc}")
        print(f"💬 Nana (forced): {say_text}")
        return False

    if text_lower in ["exit", "thoát", "dừng lại"]:
        return True

    if handle_admin(text):
        return False

    if handle_process_command(vts, voice, text, text_lower):
        return False

    if handle_presence_command(loop, text_lower, voice=voice):
        return False

    if handle_status_command(loop, voice, text, text_lower):
        return False

    if await handle_stage_runtime_command(vts, text, text_lower):
        return False

    if handle_review_audit_command(text, text_lower):
        return False

    if handle_starter_command(text, text_lower):
        return False

    if handle_voice_command(voice, text, text_lower):
        return False

    if handle_core_identity_command(text, text_lower):
        return False

    if handle_public_stage_command(text, text_lower):
        return False

    if handle_awareness_command(text_lower):
        return False

    if text_lower.startswith("/identity") or text_lower.startswith("/identity_done"):
        return await handle_identity_command(text)

    should_skip, dup_reason, dup_count = check_duplicate_user_input(text)
    if should_skip:
        if not hasattr(check_duplicate_user_input, "_last_announce"):
            check_duplicate_user_input._last_announce = 0.0
        now_announce = time.time()
        if now_announce - check_duplicate_user_input._last_announce >= 8.0:
            check_duplicate_user_input._last_announce = now_announce
            print(
                f"⏸ Input bị lặp {dup_count} lần trong {int(LAST_USER_INPUT_DEDUP_WINDOW)}s "
                f"({dup_reason}). Bỏ qua để khỏi loop. Kiểm tra mic / hotkey / VTube."
            )
        return False

    # Tell the autonomy loop that a user command just arrived so it
    # starts its 10s cooldown. Best-effort: do not block on errors.
    autonomy_note_user_command()

    if await handle_vts_command(text_lower):
        return False

    if handle_autonomy_runtime_command(text, text_lower):
        return False

    if text_lower in {"/help", "/commands"}:
        print_command_help()
        return False

    if await handle_phase_compat_command(loop, vts, voice, text, text_lower):
        return False

    if await handle_core_phase_command(loop, vts, voice, text, text_lower):
        return False

    adapter_result = await handle_adapter_command(globals(), vts, voice, text, text_lower)
    if adapter_result is not None:
        return adapter_result
    # Stardew live supervised removed
    if handle_memory_command(text, text_lower):
        return False

    if handle_persona_command(text_lower):
        return False

    if await handle_action_diagnostic_command(loop, text, text_lower):
        return False

    if handle_stardew_adapter_command(text_lower):
        return False

    if text.startswith("/"):
        print("⚠️ Unknown command")
        print(f"  Input: {text}")
        suggestion = suggest_slash_command(text)
        if suggestion:
            print(f"  Did you mean: {suggestion}")
        print("  Hint: dùng /help để xem lệnh hiện có.")
        return False

    from nana.cli.chat_turn_pipeline import handle_chat_turn

    return await handle_chat_turn(vts, voice, text, loop, text_lower)
