"""Read-only status surface for Memory v2 Phase 2.

The status helper accepts optional result objects from the isolated Phase 2
modules. It does not load the global memory singleton, start providers, or
change feature flags.
"""

from __future__ import annotations

import os
from typing import Any


PHASE = "MEMORY-V2-PHASE2"


def phase2_flag_snapshot() -> dict[str, bool]:
    try:
        from nana import config
    except Exception:
        config = None
    names = {
        "semantic_retrieval": "MEMORY_SEMANTIC_RETRIEVAL_ENABLED",
        "consolidation_preview": "MEMORY_CONSOLIDATION_PREVIEW_ENABLED",
        "public_cross_session_recall": "MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED",
        "promotion": "MEMORY_PROMOTION_ENABLED",
    }
    values: dict[str, bool] = {}
    for key, attr in names.items():
        env_name = {
            "semantic_retrieval": "NANA_MEMORY_SEMANTIC_RETRIEVAL_ENABLED",
            "consolidation_preview": "NANA_MEMORY_CONSOLIDATION_PREVIEW_ENABLED",
            "public_cross_session_recall": "NANA_MEMORY_PUBLIC_CROSS_SESSION_RECALL_ENABLED",
            "promotion": "NANA_MEMORY_PROMOTION_ENABLED",
        }[key]
        raw = os.getenv(env_name)
        if raw is not None:
            values[key] = str(raw).strip().lower() in {"1", "true", "yes", "on"}
        else:
            values[key] = bool(getattr(config, attr, False)) if config is not None else False
    return values


def phase2_status_snapshot(*, retrieval: Any = None, consolidation: Any = None, public_recall: Any = None) -> dict[str, Any]:
    flags = phase2_flag_snapshot()
    return {
        "phase": PHASE,
        "flags": flags,
        "retrieval": retrieval.to_dict() if hasattr(retrieval, "to_dict") else None,
        "consolidation": consolidation.to_dict() if hasattr(consolidation, "to_dict") else None,
        "public_cross_session": public_recall.to_dict() if hasattr(public_recall, "to_dict") else None,
        "read_only": True,
        "can_act": False,
        "memory_write": False,
    }


def phase2_status_lines(*, retrieval: Any = None, consolidation: Any = None, public_recall: Any = None) -> list[str]:
    snapshot = phase2_status_snapshot(
        retrieval=retrieval,
        consolidation=consolidation,
        public_recall=public_recall,
    )
    flags = snapshot["flags"]
    lines = [
        f"Memory v2 Phase 2 ({PHASE})",
        "  Flags: " + " | ".join(f"{key}={value}" for key, value in flags.items()),
        "  read_only=True | can_act=False | memory_write=False",
    ]
    for key in ("retrieval", "consolidation", "public_cross_session"):
        value = snapshot.get(key)
        if isinstance(value, dict):
            lines.append(
                f"  {key}: status={value.get('status', 'unknown')} | "
                f"candidates={len(value.get('candidates', [])) if isinstance(value.get('candidates'), list) else '-'} | "
                f"actions={len(value.get('actions', [])) if isinstance(value.get('actions'), list) else '-'}"
            )
        else:
            lines.append(f"  {key}: not_run")
    lines.append("  Safety: no automatic promotion, provider, TTS, avatar, OBS, game, or livestream work.")
    return lines


__all__ = ["PHASE", "phase2_flag_snapshot", "phase2_status_snapshot", "phase2_status_lines"]
