"""nana.runtime - runtime subsystem facade.

Keep the package import light. Importing ``nana.runtime.capabilities`` or any
other runtime submodule should not eagerly load browser refresh, proactive
window probes, or local-helper summaries.
"""

from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec


_EXPORTS = {
    # metrics
    "RUNTIME_LATENCY": "nana.runtime.metrics",
    "RUNTIME_RECONCILE": "nana.runtime.metrics",
    "BROWSER_REFRESH_COOLDOWN_SECONDS": "nana.runtime.metrics",
    "BROWSER_REFRESH_INFLIGHT": "nana.runtime.metrics",
    "record_browser_refresh_latency": "nana.runtime.metrics",
    "record_browser_cache_hit": "nana.runtime.metrics",
    "record_browser_coalesced": "nana.runtime.metrics",
    "record_browser_cooldown_skip": "nana.runtime.metrics",
    "browser_refresh_last_age_seconds": "nana.runtime.metrics",
    # browser state helpers
    "browser_snapshot_state": "nana.runtime.browser_state",
    "current_browser_snapshot_state": "nana.runtime.browser_state",
    "snapshot_state_usable_for_context": "nana.runtime.browser_state",
    "snapshot_state_usable_for_social_draft": "nana.runtime.browser_state",
    # browser refresh logic
    "browser_reader": "nana.runtime.browser_refresh",
    "vision_previewer": "nana.runtime.browser_refresh",
    "browser_snapshot_from_context": "nana.runtime.browser_refresh",
    "refresh_browser_state": "nana.runtime.browser_refresh",
    "ensure_browser_snapshot": "nana.runtime.browser_refresh",
    "print_browser_state": "nana.runtime.browser_refresh",
    # proactive compatibility exports
    "PHASE35_WIN32_AVAILABLE": "nana.runtime.proactive",
    "PHASE36_IMAGEGRAB_AVAILABLE": "nana.runtime.proactive",
    "BROWSER_APPS": "nana.runtime.proactive",
    "BROWSER_FRESH_SECONDS": "nana.runtime.proactive",
    "browser_age_seconds": "nana.runtime.proactive",
    "classify_zone": "nana.runtime.proactive",
    "get_active_window": "nana.runtime.proactive",
    "idle_duration": "nana.runtime.proactive",
    "is_active_window": "nana.runtime.proactive",
    "is_browser_fresh": "nana.runtime.proactive",
    "is_idle": "nana.runtime.proactive",
    "print_phase3_state": "nana.runtime.proactive",
    "print_phase4_state": "nana.runtime.proactive",
    "reset_proactive": "nana.runtime.proactive",
    "sync_proactive": "nana.runtime.proactive",
    "update_proactive": "nana.runtime.proactive",
    # semantic avatar runtime bridge
    "AvatarIntent": "nana.runtime.avatar_intent_gateway",
    "AvatarIntentGateway": "nana.runtime.avatar_intent_gateway",
    "AvatarLookTarget": "nana.runtime.avatar_intent_gateway",
    "AvatarReceipt": "nana.runtime.avatar_intent_gateway",
    "LOOK_PRESETS": "nana.runtime.avatar_intent_gateway",
    "avatar_runtime_look_preview_lines": "nana.runtime.avatar_intent_gateway",
    "avatar_runtime_look_submit_lines": "nana.runtime.avatar_intent_gateway",
    "avatar_runtime_preview_lines": "nana.runtime.avatar_intent_gateway",
    "avatar_runtime_status_lines": "nana.runtime.avatar_intent_gateway",
    "avatar_runtime_submit_lines": "nana.runtime.avatar_intent_gateway",
    "get_avatar_intent_gateway": "nana.runtime.avatar_intent_gateway",
    "issue_avatar_action": "nana.runtime.avatar_intent_gateway",
    "issue_avatar_look": "nana.runtime.avatar_intent_gateway",
    "look_payload_for_preset": "nana.runtime.avatar_intent_gateway",
    "AvatarMouthStream": "nana.runtime.avatar_mouth_stream",
    "get_avatar_mouth_stream": "nana.runtime.avatar_mouth_stream",
    # deduplication compatibility exports
    "BROWSER_CONTEXT_MARKERS": "nana.runtime.deduplication",
    "DEDUP_SUMMARY": "nana.runtime.deduplication",
    "is_browser_context_question": "nana.runtime.deduplication",
    "_SHARED_MARKERS": "nana.runtime.deduplication",
    "_LIVE_ONLY_EXTRA_MARKERS": "nana.runtime.deduplication",
    # public grounding helpers (P1-B) - re-exported from memory_grounding
    "PublicRecallDecision": "nana.runtime.memory_grounding",
    "PublicGroundingDecision": "nana.runtime.memory_grounding",
    "assemble_public_grounding_prompt": "nana.runtime.memory_grounding",
    "verify_public_response": "nana.runtime.memory_grounding",
    "verify_public_grounding_reply": "nana.runtime.memory_grounding",
    "filter_public_candidates": "nana.runtime.memory_grounding",
    "filter_public_safe_candidates": "nana.runtime.memory_grounding",
    "make_public_grounding_decision": "nana.runtime.memory_grounding",
    "format_public_grounding_prompt": "nana.runtime.memory_grounding",
    "get_safe_public_answer": "nana.runtime.memory_grounding",
    "is_direct_fact_question": "nana.runtime.memory_grounding",
    "PUBLIC_FACT_FACETS": "nana.runtime.memory_grounding",
    "infer_public_query_facet": "nana.runtime.memory_grounding",
    "infer_public_query_facets": "nana.runtime.memory_grounding",
    "infer_public_candidate_facet": "nana.runtime.memory_grounding",
    "query_targets_public_viewer": "nana.runtime.memory_grounding",
    "candidate_has_personal_predicate": "nana.runtime.memory_grounding",
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    module_name = _EXPORTS.get(name)
    if module_name:
        return getattr(import_module(module_name), name)
    submodule_name = f"{__name__}.{name}"
    if find_spec(submodule_name) is not None:
        return import_module(submodule_name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(set(globals()) | set(_EXPORTS))
