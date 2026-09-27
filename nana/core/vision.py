"""nana.core.vision — shared vision state for new-runtime commands."""
from __future__ import annotations

LAST_VISION_DESCRIPTION = None


def set_last_vision_description(value):
    global LAST_VISION_DESCRIPTION
    LAST_VISION_DESCRIPTION = value
    return LAST_VISION_DESCRIPTION


def clear_last_vision_description():
    return set_last_vision_description(None)
