"""nana.core.recovery — stable recovery facade."""
from __future__ import annotations


def recovery_clear_social(reason="social_draft_created"):
    from nana.runtime.recovery import recovery_clear_social as _clear_social

    return _clear_social(reason=reason)


def recovery_notice(reason="", detail="", cooldown=True):
    from nana.runtime.persona import persona_state_snapshot
    from nana.runtime.recovery import recovery_message

    mode = (persona_state_snapshot() or {}).get("mode", "chill")
    return recovery_message(reason, detail=detail, cooldown=cooldown, mode=mode)
