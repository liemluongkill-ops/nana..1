"""nana.core.context_budget — stable context budget facade."""
from __future__ import annotations


def build_context_budget_preview(context, level="L2"):
    from nana.actions.privacy import build_context_budget_preview as _build_preview

    return _build_preview(context, level=level)
