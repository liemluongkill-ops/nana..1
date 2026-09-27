"""nana.core.routing — stable routing facade for the new runtime."""
from __future__ import annotations


def route_sidecar_task(text, context=None):
    from nana.brain.model_router import route_sidecar_task as _route_sidecar_task

    return _route_sidecar_task(text, context=context)
