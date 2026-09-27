"""Compatibility facade for Nana status commands.

The real status panels live in smaller modules so CLI routers can lazy-load only
what a command needs.  Keep this module for old smoke tests and imports that
still use ``from nana.core.status import ...``.
"""

from __future__ import annotations

from nana.core.status_public import (
    print_core_self_status,
    print_external_bridge_status,
    print_external_bridge_test,
    print_lane_affect_status,
    print_llm_route_bakeoff,
    print_llm_route_probe,
    print_llm_route_status,
    print_memory_grounding_status,
    print_persona_boundary_status,
    print_persona_boundary_test,
    print_persona_spine_status,
    print_public_quality_preview,
    print_public_quality_status,
    print_public_stage_preview,
    print_public_stage_status,
    print_social_session_status,
    print_social_starter_preview,
    print_social_starter_status,
    print_viewer_chat_clear,
    print_viewer_chat_status,
    print_viewer_chat_test,
)
from nana.core.status_runtime import (
    print_attention_state,
    print_autonomy_lock_status,
    print_focus_state,
    print_nana_status,
    print_persona_status,
    print_presence_state,
    print_queue_state,
    print_recovery_status,
    print_recovery_summary,
    print_residue_status,
    print_runtime_reconcile_line,
    print_runtime_status,
    print_time_state,
    print_vts_expression_policy_status,
)
from nana.core.status_stage import print_stage_status
from nana.core.status_voice import print_stage_output_status, print_subtitle_status, print_voice_status

__all__ = [
    "print_attention_state",
    "print_autonomy_lock_status",
    "print_core_self_status",
    "print_external_bridge_status",
    "print_external_bridge_test",
    "print_focus_state",
    "print_lane_affect_status",
    "print_llm_route_bakeoff",
    "print_llm_route_probe",
    "print_llm_route_status",
    "print_memory_grounding_status",
    "print_nana_status",
    "print_persona_boundary_status",
    "print_persona_boundary_test",
    "print_persona_spine_status",
    "print_persona_status",
    "print_presence_state",
    "print_public_quality_preview",
    "print_public_quality_status",
    "print_public_stage_preview",
    "print_public_stage_status",
    "print_queue_state",
    "print_recovery_status",
    "print_recovery_summary",
    "print_residue_status",
    "print_runtime_reconcile_line",
    "print_runtime_status",
    "print_social_session_status",
    "print_social_starter_preview",
    "print_social_starter_status",
    "print_stage_output_status",
    "print_stage_status",
    "print_subtitle_status",
    "print_time_state",
    "print_viewer_chat_clear",
    "print_viewer_chat_status",
    "print_viewer_chat_test",
    "print_voice_status",
    "print_vts_expression_policy_status",
]
