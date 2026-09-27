"""nana.core.autonomy — stable autonomy safety facade."""
from __future__ import annotations


def autonomy_lock_block_reason(intent_name):
    try:
        from nana.autonomy.lock import autonomy_lock_block_reason as _block_reason
    except Exception:
        return None
    return _block_reason(intent_name)
