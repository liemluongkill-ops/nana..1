"""nana.intent.planner — intent planner facade for the new runtime."""
from __future__ import annotations


def plan_intent(raw_text, context=None):
    from nana.actions.intent import plan_intent as _plan_intent

    return _plan_intent(raw_text, context=context)
