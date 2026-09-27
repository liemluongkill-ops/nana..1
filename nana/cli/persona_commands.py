"""Persona state command routing for the CLI."""

from __future__ import annotations


def handle_persona_command(text_lower: str) -> bool:
    if text_lower in {"/state-log", "/vibe-log", "/persona-log"}:
        from nana.runtime.persona import format_transition_log

        for line in format_transition_log():
            print(line)
        return True

    if text_lower in {"/clear-state-log", "/clear-vibe-log"}:
        from nana.runtime.persona import clear_transition_log, format_transition_log

        clear_transition_log()
        print("🧹 State transition log đã xóa")
        for line in format_transition_log():
            print(line)
        return True

    if text_lower in {"/reset-vibe", "/vibe-reset"}:
        from nana.runtime.persona import format_persona_status, reset_persona

        reset_persona()
        print("🔄 Persona đã reset về chill/neutral")
        for line in format_persona_status():
            print(line)
        return True

    if text_lower in {"/focus-mode", "/focusmode"}:
        from nana.runtime.persona import format_persona_status, set_persona_mode

        set_persona_mode("focus", reason="manual_focus", minutes=30)
        print("🎯 Persona mode: focus")
        for line in format_persona_status():
            print(line)
        return True

    if text_lower in {"/technical-mode", "/tech-mode", "/debug-mode"}:
        from nana.runtime.persona import format_persona_status, set_persona_mode

        set_persona_mode("technical", reason="manual_technical", minutes=30)
        print("🛠️ Persona mode: technical")
        for line in format_persona_status():
            print(line)
        return True

    if text_lower in {"/social-mode", "/socialmode"}:
        from nana.runtime.persona import format_persona_status, set_persona_mode

        set_persona_mode("social", reason="manual_social", minutes=20)
        print("🌐 Persona mode: social")
        for line in format_persona_status():
            print(line)
        return True

    if text_lower in {"/chill-mode", "/chillmode"}:
        from nana.runtime.persona import format_persona_status, set_persona_mode

        set_persona_mode("chill", reason="manual_chill", minutes=0)
        print("🍵 Persona mode: chill")
        for line in format_persona_status():
            print(line)
        return True

    return False


__all__ = ["handle_persona_command"]
