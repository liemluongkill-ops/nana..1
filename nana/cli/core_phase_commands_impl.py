"""Core/live phase command router for Nana CLI.

This owns the phase7-phase18 and phase81-phase85 command surface outside the
main chat dispatcher. Phase modules are imported lazily only after a matching
slash command is detected, so normal chat does not drag the whole phase graph
into the hot path.
"""

from __future__ import annotations

from importlib import import_module

from nana.cli import globals as cli_globals


def _ensure_browser_snapshot(loop, **kwargs):
    return import_module("nana.runtime.browser_refresh").ensure_browser_snapshot(loop, **kwargs)


_CORE_PHASE_EXACT_COMMANDS = frozenset({
    '/action-contract',
    '/action-trace',
    '/action-trace-guard-status',
    '/anti-lore-status',
    '/anti-lore-test',
    '/attention',
    '/attention-memory-guard-status',
    '/attention-memory-status',
    '/attention-memory-test',
    '/attention-rhythm-guard-status',
    '/attention-rhythm-status',
    '/attention-rhythm-test',
    '/attention-state-guard-status',
    '/attention-state-status',
    '/attention-state-test',
    '/attention-status',
    '/audit-clear',
    '/audit-guard-status',
    '/audit-log',
    '/audit-replay',
    '/audit-review',
    '/autonomy-lock',
    '/bounded-pilot-guard-status',
    '/bounded-pilot-status',
    '/bounded-pilot-test',
    '/broker-guard-status',
    '/broker-matrix',
    '/clear-recovery',
    '/cns-gate-guard-status',
    '/cns-gate-status',
    '/cns-gate-test',
    '/cns-inventory',
    '/cns-map',
    '/command-normalize-test',
    '/command-route-test',
    '/command-route-truth-status',
    '/command-router-guard-status',
    '/command-router-status',
    '/command-truth',
    '/command-truth-status',
    '/companion-consistency-guard-status',
    '/companion-consistency-status',
    '/companion-consistency-test',
    '/companion-habit-status',
    '/companion-habit-test',
    '/companion-integration-guard-status',
    '/companion-integration-status',
    '/companion-integration-test',
    '/companion-live-guard-status',
    '/companion-live-status',
    '/companion-live-test',
    '/companion-response-guard-status',
    '/companion-response-status',
    '/companion-response-test',
    '/companion-safety-guard-status',
    '/companion-safety-status',
    '/companion-safety-test',
    '/continuity-candidate-status',
    '/continuity-candidate-test',
    '/conversation-rhythm-check',
    '/conversation-rhythm-guard-status',
    '/conversation-rhythm-status',
    '/conversation-rhythm-test',
    '/daily-frame-guard-status',
    '/daily-frame-status',
    '/daily-frame-test',
    '/daily-loop-guard-status',
    '/daily-loop-status',
    '/daily-loop-test',
    '/day-continuity-guard-status',
    '/day-continuity-status',
    '/day-continuity-test',
    '/dialogue-drift-check',
    '/dialogue-drift-guard-status',
    '/dialogue-drift-status',
    '/dialogue-drift-test',
    '/dialogue-energy-guard-status',
    '/dialogue-energy-status',
    '/dialogue-energy-test',
    '/dialogue-gate-guard-status',
    '/dialogue-gate-status',
    '/dialogue-gate-test',
    '/dry-run',
    '/dry-run-expect',
    '/dry-run-guard-status',
    '/dry-run-last',
    '/dry-run-quality',
    '/event-log',
    '/event-log-clear',
    '/event-store-guard-status',
    '/event-store-status',
    '/executor-rehearsal-guard-status',
    '/executor-rehearsal-status',
    '/executor-rehearsal-test',
    '/grounded-reflection-guard-status',
    '/grounded-reflection-status',
    '/grounded-reflection-test',
    '/habit-candidate-guard-status',
    '/habit-candidate-status',
    '/habit-candidate-test',
    '/kill-switch-status',
    '/kill-switch-test',
    '/long-session-status',
    '/memory-conflict-guard-status',
    '/memory-conflict-status',
    '/memory-conflict-test',
    '/memory-decay-guard-status',
    '/memory-decay-status',
    '/memory-decay-test',
    '/memory-gate-guard-status',
    '/memory-gate-status',
    '/memory-gate-test',
    '/memory-governance-guard-status',
    '/memory-governance-status',
    '/memory-governance-test',
    '/memory-v3-guard-status',
    '/memory-v3-status',
    '/memory-v3-test',
    '/nana-boundary-guard-status',
    '/nana-boundary-status',
    '/nana-boundary-test',
    '/nana-level5-map',
    '/nana-policy-map-guard-status',
    '/nana-policy-map-status',
    '/nana-policy-map-test',
    '/nana-priority-guard-status',
    '/nana-priority-status',
    '/nana-priority-test',
    '/nana-runtime-state-guard-status',
    '/nana-runtime-state-status',
    '/nana-runtime-state-test',
    '/nana-stream-guard-status',
    '/nana-stream-status',
    '/nana-stream-test',
    '/output-candidate-guard-status',
    '/output-candidate-status',
    '/output-candidate-test',
    '/output-gate-guard-status',
    '/output-gate-status',
    '/output-gate-test',
    '/p10',
    '/p10-1',
    '/p10-1-ready',
    '/p10-10',
    '/p10-10-ready',
    '/p10-2',
    '/p10-2-ready',
    '/p10-3',
    '/p10-3-ready',
    '/p10-4',
    '/p10-4-ready',
    '/p10-5',
    '/p10-5-ready',
    '/p10-6',
    '/p10-6-ready',
    '/p10-7',
    '/p10-7-ready',
    '/p10-8',
    '/p10-8-ready',
    '/p10-9',
    '/p10-9-ready',
    '/p10-ready',
    '/p11',
    '/p11-1',
    '/p11-1-ready',
    '/p11-10',
    '/p11-10-ready',
    '/p11-2',
    '/p11-2-ready',
    '/p11-3',
    '/p11-3-ready',
    '/p11-4',
    '/p11-4-ready',
    '/p11-5',
    '/p11-5-ready',
    '/p11-6',
    '/p11-6-ready',
    '/p11-7',
    '/p11-7-ready',
    '/p11-8',
    '/p11-8-ready',
    '/p11-9',
    '/p11-9-ready',
    '/p11-ready',
    '/p12',
    '/p12-1',
    '/p12-1-ready',
    '/p12-2',
    '/p12-2-ready',
    '/p12-3',
    '/p12-3-ready',
    '/p12-4',
    '/p12-4-ready',
    '/p12-5',
    '/p12-5-ready',
    '/p12-ready',
    '/p13',
    '/p13-1',
    '/p13-1-ready',
    '/p13-2',
    '/p13-2-ready',
    '/p13-3',
    '/p13-3-ready',
    '/p13-4',
    '/p13-4-ready',
    '/p13-5',
    '/p13-5-ready',
    '/p13-ready',
    '/p14',
    '/p14-1',
    '/p14-1-ready',
    '/p14-2',
    '/p14-2-ready',
    '/p14-3',
    '/p14-3-ready',
    '/p14-4',
    '/p14-4-ready',
    '/p14-5',
    '/p14-5-ready',
    '/p14-ready',
    '/p15',
    '/p15-1',
    '/p15-1-ready',
    '/p15-2',
    '/p15-2-ready',
    '/p15-3',
    '/p15-3-ready',
    '/p15-4',
    '/p15-4-ready',
    '/p15-5',
    '/p15-5-ready',
    '/p15-ready',
    '/p16',
    '/p16-1',
    '/p16-1-ready',
    '/p16-2',
    '/p16-2-ready',
    '/p16-3',
    '/p16-3-ready',
    '/p16-4',
    '/p16-4-ready',
    '/p16-5',
    '/p16-5-ready',
    '/p16-ready',
    '/p17',
    '/p17-1',
    '/p17-1-ready',
    '/p17-2',
    '/p17-2-ready',
    '/p17-3',
    '/p17-3-ready',
    '/p17-4',
    '/p17-4-ready',
    '/p17-ready',
    '/p18',
    '/p18-1',
    '/p18-1-ready',
    '/p18-2',
    '/p18-2-ready',
    '/p18-3',
    '/p18-3-ready',
    '/p18-4',
    '/p18-4-ready',
    '/p18-ready',
    '/p7',
    '/p7-ready',
    '/p8',
    '/p8-ready',
    '/p81',
    '/p81-ready',
    '/p82',
    '/p82-ready',
    '/p83',
    '/p83-ready',
    '/p84',
    '/p84-ready',
    '/p85',
    '/p85-ready',
    '/p9',
    '/p9-ready',
    '/permission-ledger-guard-status',
    '/permission-ledger-status',
    '/permission-ledger-test',
    '/persona-status',
    '/phase10-1-guard-status',
    '/phase10-1-ready',
    '/phase10-1-status',
    '/phase10-10-guard-status',
    '/phase10-10-ready',
    '/phase10-10-status',
    '/phase10-10-test',
    '/phase10-2-guard-status',
    '/phase10-2-ready',
    '/phase10-2-status',
    '/phase10-3-guard-status',
    '/phase10-3-ready',
    '/phase10-3-status',
    '/phase10-4-guard-status',
    '/phase10-4-ready',
    '/phase10-4-status',
    '/phase10-5-guard-status',
    '/phase10-5-ready',
    '/phase10-5-status',
    '/phase10-6-guard-status',
    '/phase10-6-ready',
    '/phase10-6-status',
    '/phase10-7-guard-status',
    '/phase10-7-ready',
    '/phase10-7-status',
    '/phase10-8-guard-status',
    '/phase10-8-ready',
    '/phase10-8-status',
    '/phase10-9-guard-status',
    '/phase10-9-ready',
    '/phase10-9-status',
    '/phase10-checklist',
    '/phase10-guard-status',
    '/phase10-ready',
    '/phase10-status',
    '/phase11-1-guard-status',
    '/phase11-1-ready',
    '/phase11-1-status',
    '/phase11-1-test',
    '/phase11-10-guard-status',
    '/phase11-10-ready',
    '/phase11-10-status',
    '/phase11-10-test',
    '/phase11-2-guard-status',
    '/phase11-2-ready',
    '/phase11-2-status',
    '/phase11-2-test',
    '/phase11-3-guard-status',
    '/phase11-3-ready',
    '/phase11-3-status',
    '/phase11-3-test',
    '/phase11-4-guard-status',
    '/phase11-4-ready',
    '/phase11-4-status',
    '/phase11-4-test',
    '/phase11-5-guard-status',
    '/phase11-5-ready',
    '/phase11-5-status',
    '/phase11-5-test',
    '/phase11-6-guard-status',
    '/phase11-6-ready',
    '/phase11-6-status',
    '/phase11-6-test',
    '/phase11-7-guard-status',
    '/phase11-7-ready',
    '/phase11-7-status',
    '/phase11-7-test',
    '/phase11-8-guard-status',
    '/phase11-8-ready',
    '/phase11-8-status',
    '/phase11-8-test',
    '/phase11-9-guard-status',
    '/phase11-9-ready',
    '/phase11-9-status',
    '/phase11-9-test',
    '/phase11-gate-guard-status',
    '/phase11-gate-status',
    '/phase11-gate-test',
    '/phase11-ready',
    '/phase11-status',
    '/phase12-1-guard-status',
    '/phase12-1-ready',
    '/phase12-1-status',
    '/phase12-1-test',
    '/phase12-2-guard-status',
    '/phase12-2-ready',
    '/phase12-2-status',
    '/phase12-2-test',
    '/phase12-3-guard-status',
    '/phase12-3-ready',
    '/phase12-3-status',
    '/phase12-3-test',
    '/phase12-4-guard-status',
    '/phase12-4-ready',
    '/phase12-4-status',
    '/phase12-4-test',
    '/phase12-5-guard-status',
    '/phase12-5-ready',
    '/phase12-5-status',
    '/phase12-5-test',
    '/phase12-ready',
    '/phase12-status',
    '/phase13-1-guard-status',
    '/phase13-1-ready',
    '/phase13-1-status',
    '/phase13-1-test',
    '/phase13-2-guard-status',
    '/phase13-2-ready',
    '/phase13-2-status',
    '/phase13-2-test',
    '/phase13-3-guard-status',
    '/phase13-3-ready',
    '/phase13-3-status',
    '/phase13-3-test',
    '/phase13-4-guard-status',
    '/phase13-4-ready',
    '/phase13-4-status',
    '/phase13-4-test',
    '/phase13-5-guard-status',
    '/phase13-5-ready',
    '/phase13-5-status',
    '/phase13-5-test',
    '/phase13-ready',
    '/phase13-status',
    '/phase14-1-guard-status',
    '/phase14-1-ready',
    '/phase14-1-status',
    '/phase14-1-test',
    '/phase14-2-guard-status',
    '/phase14-2-ready',
    '/phase14-2-status',
    '/phase14-2-test',
    '/phase14-3-guard-status',
    '/phase14-3-ready',
    '/phase14-3-status',
    '/phase14-3-test',
    '/phase14-4-guard-status',
    '/phase14-4-ready',
    '/phase14-4-status',
    '/phase14-4-test',
    '/phase14-5-guard-status',
    '/phase14-5-ready',
    '/phase14-5-status',
    '/phase14-5-test',
    '/phase14-ready',
    '/phase14-status',
    '/phase15-1-guard-status',
    '/phase15-1-ready',
    '/phase15-1-status',
    '/phase15-1-test',
    '/phase15-2-guard-status',
    '/phase15-2-ready',
    '/phase15-2-status',
    '/phase15-2-test',
    '/phase15-3-guard-status',
    '/phase15-3-ready',
    '/phase15-3-status',
    '/phase15-3-test',
    '/phase15-4-guard-status',
    '/phase15-4-ready',
    '/phase15-4-status',
    '/phase15-4-test',
    '/phase15-5-guard-status',
    '/phase15-5-ready',
    '/phase15-5-status',
    '/phase15-5-test',
    '/phase15-ready',
    '/phase15-status',
    '/phase16-1-guard-status',
    '/phase16-1-ready',
    '/phase16-1-status',
    '/phase16-1-test',
    '/phase16-2-guard-status',
    '/phase16-2-ready',
    '/phase16-2-status',
    '/phase16-2-test',
    '/phase16-3-guard-status',
    '/phase16-3-ready',
    '/phase16-3-status',
    '/phase16-3-test',
    '/phase16-4-guard-status',
    '/phase16-4-ready',
    '/phase16-4-status',
    '/phase16-4-test',
    '/phase16-5-guard-status',
    '/phase16-5-ready',
    '/phase16-5-status',
    '/phase16-5-test',
    '/phase16-ready',
    '/phase16-status',
    '/phase17-1-guard-status',
    '/phase17-1-ready',
    '/phase17-1-status',
    '/phase17-1-test',
    '/phase17-2-guard-status',
    '/phase17-2-ready',
    '/phase17-2-status',
    '/phase17-2-test',
    '/phase17-3-guard-status',
    '/phase17-3-ready',
    '/phase17-3-status',
    '/phase17-3-test',
    '/phase17-4-guard-status',
    '/phase17-4-ready',
    '/phase17-4-status',
    '/phase17-4-test',
    '/phase17-ready',
    '/phase17-status',
    '/phase18-1-guard-status',
    '/phase18-1-ready',
    '/phase18-1-status',
    '/phase18-1-test',
    '/phase18-2-guard-status',
    '/phase18-2-ready',
    '/phase18-2-status',
    '/phase18-2-test',
    '/phase18-3-guard-status',
    '/phase18-3-ready',
    '/phase18-3-status',
    '/phase18-3-test',
    '/phase18-4-guard-status',
    '/phase18-4-ready',
    '/phase18-4-status',
    '/phase18-4-test',
    '/phase18-ready',
    '/phase18-status',
    '/phase7-dry-run',
    '/phase7-guard-status',
    '/phase7-log',
    '/phase7-pending',
    '/phase7-pending-guard-status',
    '/phase7-quality',
    '/phase7-ready',
    '/phase7-status',
    '/phase8-action-trace-guard-status',
    '/phase8-broker-matrix',
    '/phase8-guard-status',
    '/phase8-pre-exec',
    '/phase8-pre-exec-guard-status',
    '/phase8-ready',
    '/phase8-status',
    '/phase81-guard-status',
    '/phase81-ready',
    '/phase81-status',
    '/phase81-test',
    '/phase82-guard-status',
    '/phase82-ready',
    '/phase82-status',
    '/phase82-test',
    '/phase83-guard-status',
    '/phase83-ready',
    '/phase83-status',
    '/phase83-test',
    '/phase84-guard-status',
    '/phase84-ready',
    '/phase84-status',
    '/phase84-test',
    '/phase85-guard-status',
    '/phase85-ready',
    '/phase85-status',
    '/phase85-test',
    '/phase9-clear',
    '/phase9-guard-status',
    '/phase9-log',
    '/phase9-ready',
    '/phase9-replay',
    '/phase9-review',
    '/phase9-status',
    '/plan-cancel',
    '/plan-confirm',
    '/plan-guard-status',
    '/plan-log',
    '/plan-pending',
    '/plan-preview',
    '/plan-propose',
    '/pre-exec-check',
    '/pre-exec-guard-status',
    '/presence-entropy-guard-status',
    '/presence-entropy-status',
    '/presence-entropy-test',
    '/presence-gate-guard-status',
    '/presence-gate-status',
    '/presence-gate-test',
    '/presence-stability-guard-status',
    '/presence-stability-status',
    '/presence-stability-test',
    '/queue-core-guard-status',
    '/queue-core-status',
    '/recovery',
    '/recovery-clear',
    '/recovery-continuity-guard-status',
    '/recovery-continuity-status',
    '/recovery-continuity-test',
    '/recovery-governor-guard-status',
    '/recovery-governor-status',
    '/recovery-status',
    '/recovery-test',
    '/reflection-injection-guard-status',
    '/reflection-injection-status',
    '/reflection-injection-test',
    '/reflection-safety-guard-status',
    '/reflection-safety-status',
    '/reflection-safety-test',
    '/reflective-gate-guard-status',
    '/reflective-gate-status',
    '/reflective-gate-test',
    '/reflective-presence-status',
    '/reflective-state-guard-status',
    '/reflective-state-status',
    '/reflective-state-test',
    '/residue',
    '/residue-status',
    '/response-shape-guard-status',
    '/response-shape-status',
    '/response-shape-test',
    '/runtime-inventory',
    '/runtime-map',
    '/runtime-state-schema',
    '/runtime-stress-guard-status',
    '/runtime-stress-status',
    '/runtime-stress-test',
    '/sandbox-boundary-guard-status',
    '/sandbox-boundary-status',
    '/sandbox-boundary-test',
    '/scheduler-guard-status',
    '/scheduler-status',
    '/scheduler-test',
    '/session-atmosphere-status',
    '/session-atmosphere-test',
    '/session-review-guard-status',
    '/session-review-status',
    '/session-review-test',
    '/shared-experience-guard-status',
    '/shared-experience-status',
    '/shared-experience-test',
    '/shared-recall-guard-status',
    '/shared-recall-status',
    '/shared-recall-test',
    '/ship-checklist',
    '/silence-hold-guard-status',
    '/silence-hold-status',
    '/silence-hold-test',
    '/social-vision-decouple-guard-status',
    '/social-vision-decouple-status',
    '/social-vision-decouple-test',
    '/state-schema',
    '/state-schema-guard-status',
    '/target-lock-guard-status',
    '/target-lock-status',
    '/target-lock-test',
    '/trust-calibration-guard-status',
    '/trust-calibration-status',
    '/trust-calibration-test',
    '/vibe-status',
})

_CORE_PHASE_PREFIX_COMMANDS = (
    '/action-contract ',
    '/action-trace ',
    '/anti-lore-test ',
    '/attention-memory-test ',
    '/attention-rhythm-test ',
    '/attention-state-test ',
    '/audit-replay ',
    '/audit-review ',
    '/bounded-pilot-preview',
    '/bounded-pilot-test ',
    '/cns-gate-test ',
    '/command-normalize-test ',
    '/command-route-test ',
    '/command-truth ',
    '/companion-consistency-test ',
    '/companion-habit-test ',
    '/companion-integration-test ',
    '/companion-live-test ',
    '/companion-response-preview',
    '/companion-response-test ',
    '/companion-safety-check',
    '/companion-safety-test ',
    '/continuity-candidate-test ',
    '/conversation-rhythm-check ',
    '/conversation-rhythm-test ',
    '/daily-frame-test ',
    '/daily-loop-test ',
    '/day-continuity-test ',
    '/dialogue-drift-check ',
    '/dialogue-drift-test ',
    '/dialogue-energy-test ',
    '/dialogue-gate-test ',
    '/dry-run ',
    '/dry-run-expect ',
    '/dry-run-quality ',
    '/dry-run-show',
    '/event-replay',
    '/executor-rehearsal-test ',
    '/grounded-reflection-test ',
    '/habit-candidate-test ',
    '/kill-switch-test ',
    '/memory-conflict-test ',
    '/memory-decay-test ',
    '/memory-gate-test ',
    '/memory-governance-test ',
    '/memory-v3-test ',
    '/nana-boundary-test ',
    '/nana-policy-map-test ',
    '/nana-priority-test ',
    '/nana-runtime-state-test ',
    '/nana-stream-mode',
    '/nana-stream-test ',
    '/output-candidate-preview',
    '/output-candidate-test ',
    '/output-gate-test ',
    '/permission-ledger-row',
    '/permission-ledger-test ',
    '/phase10-10-test ',
    '/phase11-1-test ',
    '/phase11-10-test ',
    '/phase11-2-test ',
    '/phase11-3-test ',
    '/phase11-4-test ',
    '/phase11-5-test ',
    '/phase11-6-test ',
    '/phase11-7-test ',
    '/phase11-8-test ',
    '/phase11-9-test ',
    '/phase11-gate-test ',
    '/phase12-1-test ',
    '/phase12-2-test ',
    '/phase12-3-test ',
    '/phase12-4-test ',
    '/phase12-5-test ',
    '/phase13-1-test ',
    '/phase13-2-test ',
    '/phase13-3-test ',
    '/phase13-4-test ',
    '/phase13-5-test ',
    '/phase14-1-test ',
    '/phase14-2-test ',
    '/phase14-3-test ',
    '/phase14-4-test ',
    '/phase14-5-test ',
    '/phase15-1-test ',
    '/phase15-2-test ',
    '/phase15-3-test ',
    '/phase15-4-test ',
    '/phase15-5-test ',
    '/phase16-1-test ',
    '/phase16-2-test ',
    '/phase16-3-test ',
    '/phase16-4-test ',
    '/phase16-5-test ',
    '/phase17-1-test ',
    '/phase17-2-test ',
    '/phase17-3-test ',
    '/phase17-4-test ',
    '/phase18-1-test ',
    '/phase18-2-test ',
    '/phase18-3-test ',
    '/phase18-4-test ',
    '/phase7-dry-run ',
    '/phase7-quality ',
    '/phase8-pre-exec ',
    '/phase81-test ',
    '/phase82-test ',
    '/phase83-test ',
    '/phase84-test ',
    '/phase85-test ',
    '/phase9-replay ',
    '/phase9-review ',
    '/plan-cancel ',
    '/plan-confirm ',
    '/plan-preview ',
    '/plan-propose ',
    '/pre-exec-check ',
    '/presence-entropy-test ',
    '/presence-gate-test ',
    '/presence-stability-test ',
    '/recovery-continuity-test ',
    '/recovery-test ',
    '/reflection-injection-preview',
    '/reflection-injection-test ',
    '/reflection-safety-check',
    '/reflection-safety-test ',
    '/reflective-gate-test ',
    '/reflective-state-test ',
    '/response-shape-test ',
    '/runtime-stress-test ',
    '/sandbox-boundary-test ',
    '/scheduler-test ',
    '/session-atmosphere-test ',
    '/session-review-preview',
    '/session-review-test ',
    '/shared-experience-test ',
    '/shared-recall-preview',
    '/shared-recall-test ',
    '/silence-hold-preview',
    '/silence-hold-test ',
    '/social-vision-decouple-test ',
    '/target-lock-test ',
    '/trust-calibration-test ',
)


def _is_core_phase_command(text_lower: str) -> bool:
    return text_lower in _CORE_PHASE_EXACT_COMMANDS or any(
        text_lower.startswith(prefix) for prefix in _CORE_PHASE_PREFIX_COMMANDS
    )


async def handle_core_phase_command(loop, vts, voice, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()
    if not _is_core_phase_command(text_lower):
        return False

    from nana.phases import (
        find_phase7_dry_run,
        phase7_dry_run_expect_report,
        phase82_normalize_stream_mode,
        print_attention_memory_guard_status,
        print_attention_memory_status,
        print_attention_memory_test,
        print_attention_rhythm_guard_status,
        print_attention_rhythm_status,
        print_attention_rhythm_test,
        print_attention_state_guard_status,
        print_attention_state_status,
        print_attention_state_test,
        print_bounded_pilot_guard_status,
        print_bounded_pilot_preview,
        print_bounded_pilot_status,
        print_bounded_pilot_test,
        print_cns_gate_guard_status,
        print_cns_gate_status,
        print_cns_gate_test,
        print_command_normalize_test,
        print_command_route_test,
        print_command_router_guard_status,
        print_command_router_status,
        print_command_truth,
        print_command_truth_status,
        print_companion_consistency_guard_status,
        print_companion_consistency_status,
        print_companion_consistency_test,
        print_companion_integration_guard_status,
        print_companion_integration_status,
        print_companion_integration_test,
        print_companion_live_guard_status,
        print_companion_live_status,
        print_companion_live_test,
        print_companion_response_guard_status,
        print_companion_response_preview,
        print_companion_response_status,
        print_companion_response_test,
        print_companion_safety_check,
        print_companion_safety_guard_status,
        print_companion_safety_status,
        print_companion_safety_test,
        print_conversation_rhythm_check,
        print_conversation_rhythm_guard_status,
        print_conversation_rhythm_status,
        print_conversation_rhythm_test,
        print_daily_frame_guard_status,
        print_daily_frame_status,
        print_daily_frame_test,
        print_daily_loop_guard_status,
        print_daily_loop_status,
        print_daily_loop_test,
        print_day_continuity_guard_status,
        print_day_continuity_status,
        print_day_continuity_test,
        print_dialogue_drift_check,
        print_dialogue_drift_guard_status,
        print_dialogue_drift_status,
        print_dialogue_drift_test,
        print_dialogue_energy_guard_status,
        print_dialogue_energy_status,
        print_dialogue_energy_test,
        print_dialogue_gate_guard_status,
        print_dialogue_gate_status,
        print_dialogue_gate_test,
        print_event_store_status,
        print_executor_rehearsal_guard_status,
        print_executor_rehearsal_status,
        print_executor_rehearsal_test,
        print_grounded_reflection_guard_status,
        print_grounded_reflection_status,
        print_grounded_reflection_test,
        print_habit_candidate_guard_status,
        print_habit_candidate_status,
        print_habit_candidate_test,
        print_memory_conflict_guard_status,
        print_memory_conflict_status,
        print_memory_conflict_test,
        print_memory_decay_guard_status,
        print_memory_decay_status,
        print_memory_decay_test,
        print_memory_gate_guard_status,
        print_memory_gate_status,
        print_memory_gate_test,
        print_memory_governance_guard_status,
        print_memory_governance_status,
        print_memory_governance_test,
        print_memory_v3_guard_status,
        print_memory_v3_status,
        print_memory_v3_test,
        print_nana_boundary_guard_status,
        print_nana_boundary_status,
        print_nana_boundary_test,
        print_nana_level5_map,
        print_nana_policy_map_guard_status,
        print_nana_policy_map_status,
        print_nana_policy_map_test,
        print_nana_priority_guard_status,
        print_nana_priority_status,
        print_nana_priority_test,
        print_nana_runtime_state_guard_status,
        print_nana_runtime_state_status,
        print_nana_runtime_state_test,
        print_nana_stream_guard_status,
        print_nana_stream_status,
        print_nana_stream_test,
        print_output_candidate_guard_status,
        print_output_candidate_preview,
        print_output_candidate_status,
        print_output_candidate_test,
        print_output_gate_guard_status,
        print_output_gate_status,
        print_output_gate_test,
        print_permission_ledger_guard_status,
        print_permission_ledger_row,
        print_permission_ledger_status,
        print_permission_ledger_test,
        print_phase10_10_ready,
        print_phase10_10_status,
        print_phase10_1_guard_status,
        print_phase10_1_ready,
        print_phase10_1_status,
        print_phase10_2_ready,
        print_phase10_2_status,
        print_phase10_3_ready,
        print_phase10_3_status,
        print_phase10_4_guard_status,
        print_phase10_4_ready,
        print_phase10_4_status,
        print_phase10_5_ready,
        print_phase10_5_status,
        print_phase10_6_ready,
        print_phase10_6_status,
        print_phase10_7_ready,
        print_phase10_7_status,
        print_phase10_8_ready,
        print_phase10_8_status,
        print_phase10_9_ready,
        print_phase10_9_status,
        print_phase10_guard_status,
        print_phase10_ready,
        print_phase10_status,
        print_phase11_10_ready,
        print_phase11_10_status,
        print_phase11_1_ready,
        print_phase11_1_status,
        print_phase11_2_ready,
        print_phase11_2_status,
        print_phase11_3_ready,
        print_phase11_3_status,
        print_phase11_4_ready,
        print_phase11_4_status,
        print_phase11_5_ready,
        print_phase11_5_status,
        print_phase11_6_ready,
        print_phase11_6_status,
        print_phase11_7_ready,
        print_phase11_7_status,
        print_phase11_8_ready,
        print_phase11_8_status,
        print_phase11_9_ready,
        print_phase11_9_status,
        print_phase11_gate_guard_status,
        print_phase11_gate_status,
        print_phase11_gate_test,
        print_phase12_1_ready,
        print_phase12_1_status,
        print_phase12_2_ready,
        print_phase12_2_status,
        print_phase12_3_ready,
        print_phase12_3_status,
        print_phase12_4_ready,
        print_phase12_4_status,
        print_phase12_5_status,
        print_phase12_ready,
        print_phase13_1_ready,
        print_phase13_1_status,
        print_phase13_2_ready,
        print_phase13_2_status,
        print_phase13_3_ready,
        print_phase13_3_status,
        print_phase13_4_ready,
        print_phase13_4_status,
        print_phase13_5_status,
        print_phase13_ready,
        print_phase14_1_ready,
        print_phase14_1_status,
        print_phase14_2_ready,
        print_phase14_2_status,
        print_phase14_3_ready,
        print_phase14_3_status,
        print_phase14_4_ready,
        print_phase14_4_status,
        print_phase14_5_status,
        print_phase14_ready,
        print_phase15_1_ready,
        print_phase15_1_status,
        print_phase15_2_ready,
        print_phase15_2_status,
        print_phase15_3_ready,
        print_phase15_3_status,
        print_phase15_4_ready,
        print_phase15_4_status,
        print_phase15_5_status,
        print_phase15_ready,
        print_phase16_1_ready,
        print_phase16_1_status,
        print_phase16_2_ready,
        print_phase16_2_status,
        print_phase16_3_ready,
        print_phase16_3_status,
        print_phase16_4_ready,
        print_phase16_4_status,
        print_phase16_5_status,
        print_phase16_ready,
        print_phase17_1_ready,
        print_phase17_1_status,
        print_phase17_2_ready,
        print_phase17_2_status,
        print_phase17_3_ready,
        print_phase17_3_status,
        print_phase17_4_status,
        print_phase17_ready,
        print_phase18_1_ready,
        print_phase18_1_status,
        print_phase18_2_ready,
        print_phase18_2_status,
        print_phase18_3_ready,
        print_phase18_3_status,
        print_phase18_4_status,
        print_phase18_ready,
        print_phase7_action_log,
        print_phase7_dry_run,
        print_phase7_dry_run_snapshot,
        print_phase7_guard_status,
        print_phase7_pending_guard_status,
        print_phase7_pending_plan,
        print_phase7_plan_cancel,
        print_phase7_plan_confirm,
        print_phase7_plan_preview,
        print_phase7_quality,
        print_phase7_ready,
        print_phase7_status,
        print_phase81_ready,
        print_phase81_status,
        print_phase82_ready,
        print_phase82_status,
        print_phase83_ready,
        print_phase83_status,
        print_phase84_ready,
        print_phase84_status,
        print_phase85_ready,
        print_phase85_status,
        print_phase8_action_contract,
        print_phase8_action_trace,
        print_phase8_action_trace_guard_status,
        print_phase8_broker_matrix,
        print_phase8_guard_status,
        print_phase8_pre_exec_check,
        print_phase8_pre_exec_guard_status,
        print_phase8_ready,
        print_phase8_status,
        print_phase9_audit_clear,
        print_phase9_audit_log,
        print_phase9_audit_replay,
        print_phase9_audit_review,
        print_phase9_guard_status,
        print_phase9_ready,
        print_phase9_status,
        print_presence_entropy_guard_status,
        print_presence_entropy_status,
        print_presence_entropy_test,
        print_presence_gate_guard_status,
        print_presence_gate_status,
        print_presence_gate_test,
        print_presence_stability_guard_status,
        print_presence_stability_status,
        print_presence_stability_test,
        print_recovery_continuity_guard_status,
        print_recovery_continuity_status,
        print_recovery_continuity_test,
        print_recovery_governor_guard_status,
        print_recovery_governor_status,
        print_recovery_test,
        print_reflection_injection_guard_status,
        print_reflection_injection_preview,
        print_reflection_injection_status,
        print_reflection_injection_test,
        print_reflection_safety_check,
        print_reflection_safety_guard_status,
        print_reflection_safety_status,
        print_reflection_safety_test,
        print_reflective_gate_guard_status,
        print_reflective_gate_status,
        print_reflective_gate_test,
        print_reflective_state_guard_status,
        print_reflective_state_status,
        print_reflective_state_test,
        print_response_shape_guard_status,
        print_response_shape_status,
        print_response_shape_test,
        print_runtime_event_clear,
        print_runtime_event_log,
        print_runtime_event_replay,
        print_runtime_map,
        print_runtime_state_schema,
        print_runtime_stress_guard_status,
        print_runtime_stress_status,
        print_runtime_stress_test,
        print_sandbox_boundary_guard_status,
        print_sandbox_boundary_status,
        print_sandbox_boundary_test,
        print_scheduler_guard_status,
        print_scheduler_status,
        print_scheduler_test,
        print_session_review_guard_status,
        print_session_review_preview,
        print_session_review_status,
        print_session_review_test,
        print_shared_experience_guard_status,
        print_shared_experience_status,
        print_shared_experience_test,
        print_shared_recall_guard_status,
        print_shared_recall_preview,
        print_shared_recall_status,
        print_shared_recall_test,
        print_silence_hold_guard_status,
        print_silence_hold_preview,
        print_silence_hold_status,
        print_silence_hold_test,
        print_social_vision_decouple_guard_status,
        print_social_vision_decouple_status,
        print_social_vision_decouple_test,
        print_state_schema_guard_status,
        print_target_lock_guard_status,
        print_target_lock_status,
        print_target_lock_test,
        print_trust_calibration_guard_status,
        print_trust_calibration_status,
        print_trust_calibration_test,
    )

    if text_lower in {"/phase7-status", "/p7"}:
        await _ensure_browser_snapshot(loop, reason="phase7_status_cache")
        print_phase7_status()
        return True

    if text_lower in {"/phase7-ready", "/p7-ready"}:
        await _ensure_browser_snapshot(loop, reason="phase7_ready_cache")
        print_phase7_ready()
        return True

    if text_lower in {"/phase8-status", "/p8"}:
        await _ensure_browser_snapshot(loop, reason="phase8_status_cache")
        print_phase8_status()
        return True

    if text_lower in {"/phase8-ready", "/p8-ready"}:
        await _ensure_browser_snapshot(loop, reason="phase8_ready_cache")
        print_phase8_ready()
        return True

    if text_lower in {"/phase9-status", "/p9"}:
        await _ensure_browser_snapshot(loop, reason="phase9_status_cache")
        print_phase9_status()
        return True

    if text_lower in {"/phase9-ready", "/p9-ready"}:
        await _ensure_browser_snapshot(loop, reason="phase9_ready_cache")
        print_phase9_ready()
        return True

    if text_lower in {"/phase10-status", "/p10"}:
        await _ensure_browser_snapshot(loop, reason="phase10_status_cache")
        print_phase10_status()
        return True

    if text_lower in {"/phase10-ready", "/p10-ready"}:
        await _ensure_browser_snapshot(loop, reason="phase10_ready_cache")
        print_phase10_ready()
        return True

    if text_lower in {"/phase10-guard-status", "/phase10-checklist", "/ship-checklist"}:
        await _ensure_browser_snapshot(loop, reason="phase10_guard_cache")
        print_phase10_guard_status()
        return True

    if text_lower in {"/runtime-map", "/runtime-inventory", "/cns-map", "/cns-inventory"}:
        await _ensure_browser_snapshot(loop, reason="phase10_1_runtime_map_cache")
        print_runtime_map()
        return True

    if text_lower in {"/phase10-1-status", "/p10-1"}:
        await _ensure_browser_snapshot(loop, reason="phase10_1_status_cache")
        print_phase10_1_status()
        return True

    if text_lower in {"/phase10-1-ready", "/p10-1-ready"}:
        await _ensure_browser_snapshot(loop, reason="phase10_1_ready_cache")
        print_phase10_1_ready()
        return True

    if text_lower in {"/phase10-1-guard-status"}:
        await _ensure_browser_snapshot(loop, reason="phase10_1_guard_cache")
        print_phase10_1_guard_status()
        return True

    if text_lower in {"/command-router-status"}:
        print_command_router_status()
        return True

    if text_lower in {"/command-truth-status", "/command-route-truth-status"}:
        print_command_truth_status()
        return True

    if text_lower == "/command-truth":
        print_command_truth_status()
        return True

    if text_lower.startswith("/command-truth "):
        print_command_truth(text.split(" ", 1)[1])
        return True

    if text_lower in {"/phase10-2-status", "/p10-2"}:
        print_phase10_2_status()
        return True

    if text_lower in {"/command-router-guard-status", "/phase10-2-guard-status"}:
        print_command_router_guard_status()
        return True

    if text_lower in {"/phase10-2-ready", "/p10-2-ready"}:
        print_phase10_2_ready()
        return True

    if text_lower == "/command-normalize-test":
        print_command_normalize_test()
        return True

    if text_lower.startswith("/command-normalize-test "):
        print_command_normalize_test(text.split(" ", 1)[1])
        return True

    if text_lower == "/command-route-test":
        print_command_route_test()
        return True

    if text_lower.startswith("/command-route-test "):
        print_command_route_test(text.split(" ", 1)[1])
        return True

    if text_lower in {"/runtime-state-schema", "/state-schema"}:
        print_runtime_state_schema()
        return True

    if text_lower in {"/state-schema-guard-status", "/phase10-3-guard-status"}:
        print_state_schema_guard_status()
        return True

    if text_lower in {"/phase10-3-status", "/p10-3"}:
        print_phase10_3_status()
        return True

    if text_lower in {"/phase10-3-ready", "/p10-3-ready"}:
        print_phase10_3_ready()
        return True

    if text_lower in {"/event-store-status", "/phase10-4-status", "/p10-4"}:
        print_event_store_status() if text_lower == "/event-store-status" else print_phase10_4_status()
        return True

    if text_lower in {"/event-store-guard-status", "/phase10-4-guard-status"}:
        print_phase10_4_guard_status()
        return True

    if text_lower in {"/phase10-4-ready", "/p10-4-ready"}:
        print_phase10_4_ready()
        return True

    if text_lower in {"/event-log"}:
        print_runtime_event_log()
        return True

    if text_lower.startswith("/event-replay"):
        parts = text.split(maxsplit=1)
        print_runtime_event_replay(parts[1] if len(parts) > 1 else None)
        return True

    if text_lower in {"/event-log-clear"}:
        print_runtime_event_clear()
        return True

    if text_lower in {"/scheduler-status", "/queue-core-status"}:
        print_scheduler_status()
        return True

    if text_lower in {"/scheduler-guard-status", "/queue-core-guard-status", "/phase10-5-guard-status"}:
        print_scheduler_guard_status()
        return True

    if text_lower in {"/scheduler-test"}:
        print_scheduler_test()
        return True

    if text_lower.startswith("/scheduler-test "):
        print_scheduler_test(text.split(" ", 1)[1])
        return True

    if text_lower in {"/phase10-5-status", "/p10-5"}:
        print_phase10_5_status()
        return True

    if text_lower in {"/phase10-5-ready", "/p10-5-ready"}:
        print_phase10_5_ready()
        return True

    if text_lower in {"/recovery-governor-status", "/phase10-6-status", "/p10-6"}:
        print_recovery_governor_status() if text_lower == "/recovery-governor-status" else print_phase10_6_status()
        return True

    if text_lower in {"/recovery-governor-guard-status", "/phase10-6-guard-status"}:
        print_recovery_governor_guard_status()
        return True

    if text_lower in {"/recovery-test"}:
        print_recovery_test()
        return True

    if text_lower.startswith("/recovery-test "):
        print_recovery_test(text.split(" ", 1)[1])
        return True

    if text_lower in {"/phase10-6-ready", "/p10-6-ready"}:
        print_phase10_6_ready()
        return True

    if text_lower in {"/audit-guard-status", "/phase9-guard-status"}:
        print_phase9_guard_status()
        return True

    if text_lower in {"/audit-log", "/phase9-log"}:
        print_phase9_audit_log()
        return True

    if text_lower in {"/audit-clear", "/phase9-clear"}:
        print_phase9_audit_clear()
        return True

    if text_lower in {"/audit-replay", "/phase9-replay"}:
        print_phase9_audit_replay()
        return True

    if text_lower.startswith("/audit-replay ") or text_lower.startswith("/phase9-replay "):
        print_phase9_audit_replay(text.split(" ", 1)[1])
        return True

    if text_lower in {"/audit-review", "/phase9-review"}:
        print_phase9_audit_review()
        return True

    if text_lower.startswith("/audit-review ") or text_lower.startswith("/phase9-review "):
        print_phase9_audit_review(text.split(" ", 1)[1])
        return True

    if text_lower in {"/broker-matrix", "/phase8-broker-matrix"}:
        await _ensure_browser_snapshot(loop, reason="phase8_broker_matrix_cache")
        print_phase8_broker_matrix()
        return True

    if text_lower in {"/broker-guard-status", "/phase8-guard-status"}:
        print_phase8_guard_status()
        return True

    if text_lower in {"/pre-exec-guard-status", "/phase8-pre-exec-guard-status"}:
        print_phase8_pre_exec_guard_status()
        return True

    if text_lower in {"/action-trace-guard-status", "/phase8-action-trace-guard-status"}:
        print_phase8_action_trace_guard_status()
        return True

    if text_lower == "/action-trace":
        print_phase8_action_trace("")
        return True

    if text_lower.startswith("/action-trace "):
        raw_text = text.split(" ", 1)[1].strip()
        await _ensure_browser_snapshot(loop, reason="phase8_action_trace_cache")
        print_phase8_action_trace(raw_text)
        return True

    if text_lower in {"/pre-exec-check", "/phase8-pre-exec"}:
        print_phase8_pre_exec_check("")
        return True

    if text_lower.startswith("/pre-exec-check ") or text_lower.startswith("/phase8-pre-exec "):
        raw_text = text.split(" ", 1)[1]
        await _ensure_browser_snapshot(loop, reason="phase8_pre_exec_cache")
        print_phase8_pre_exec_check(raw_text)
        return True

    if text_lower == "/action-contract":
        print_phase8_action_contract("")
        return True

    if text_lower.startswith("/action-contract "):
        print_phase8_action_contract(text.split(" ", 1)[1])
        return True

    if text_lower in {"/dry-run-guard-status", "/phase7-guard-status"}:
        print_phase7_guard_status()
        return True

    if text_lower in {"/dry-run", "/phase7-dry-run"}:
        print("⚠️ Thiếu text. Ví dụ: /dry-run Nana reply tweet này")
        return True

    if text_lower.startswith("/dry-run ") or text_lower.startswith("/phase7-dry-run "):
        raw_text = text.split(" ", 1)[1].strip()
        await _ensure_browser_snapshot(loop, reason="phase7_dry_run_cache")
        print_phase7_dry_run(raw_text)
        return True

    if text_lower == "/dry-run-last":
        print_phase7_dry_run_snapshot(find_phase7_dry_run())
        return True

    if text_lower.startswith("/dry-run-show"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            print("🧾 Dry Run Snapshot")
            print("  Missing: /dry-run-show <id>")
            return True
        print_phase7_dry_run_snapshot(find_phase7_dry_run(parts[1]))
        return True

    if text_lower in {"/dry-run-quality", "/phase7-quality"}:
        print("⚠️ Thiếu text. Ví dụ: /dry-run-quality Nana đăng tweet này hộ Ba")
        return True

    if text_lower.startswith("/dry-run-quality ") or text_lower.startswith("/phase7-quality "):
        raw_text = text.split(" ", 1)[1].strip()
        await _ensure_browser_snapshot(loop, reason="phase7_quality_cache")
        print_phase7_quality(raw_text)
        return True

    if text_lower == "/dry-run-expect":
        print("⚠️ Thiếu case. Ví dụ: /dry-run-expect social.reply | Nana reply tweet này")
        return True

    if text_lower.startswith("/dry-run-expect "):
        raw_text = text.replace("/dry-run-expect", "", 1).strip()
        for line in phase7_dry_run_expect_report(raw_text):
            print(line)
        return True

    if text_lower in {"/plan-guard-status", "/phase7-pending-guard-status"}:
        print_phase7_pending_guard_status()
        return True

    if text_lower in {"/plan-log", "/phase7-log"}:
        print_phase7_action_log()
        return True

    if text_lower in {"/plan-pending", "/phase7-pending"}:
        print_phase7_pending_plan()
        return True

    if text_lower in {"/plan-preview", "/plan-propose"}:
        print("⚠️ Thiếu text. Ví dụ: /plan-preview Nana reply tweet này")
        return True

    if text_lower.startswith("/plan-preview ") or text_lower.startswith("/plan-propose "):
        raw_text = text.split(" ", 1)[1].strip()
        await _ensure_browser_snapshot(loop, reason="phase7_plan_preview_cache")
        print_phase7_plan_preview(raw_text)
        return True

    if text_lower == "/plan-confirm":
        print("⚠️ Thiếu ID. Ví dụ: /plan-confirm 1")
        return True

    if text_lower.startswith("/plan-confirm "):
        print_phase7_plan_confirm(text.split(" ", 1)[1])
        return True

    if text_lower == "/plan-cancel":
        print_phase7_plan_cancel()
        return True

    if text_lower.startswith("/plan-cancel "):
        print_phase7_plan_cancel(text.split(" ", 1)[1])
        return True

    if text_lower in {"/autonomy-lock"}:
        from nana.core.status_runtime import print_autonomy_lock_status

        print_autonomy_lock_status()
        return True

    if text_lower in {"/attention", "/attention-status"}:
        from nana.core.status_runtime import print_attention_state

        print_attention_state()
        return True

    if text_lower in {"/vibe-status", "/persona-status"}:
        from nana.runtime.persona import format_persona_status

        for line in format_persona_status():
            print(line)
        return True

    if text_lower in {"/residue-status", "/residue"}:
        from nana.core.status_runtime import print_residue_status

        print_residue_status()
        return True

    if text_lower in {"/recovery", "/recovery-status"}:
        from nana.runtime.recovery import recovery_status_lines

        for line in recovery_status_lines():
            print(line)
        return True

    if text_lower in {"/recovery-clear", "/clear-recovery"}:
        for line in recovery_clear():
            print(line)
        return True

    if text_lower in {"/memory-governance-status", "/phase10-7-status", "/p10-7"}:
        print_memory_governance_status() if text_lower == "/memory-governance-status" else print_phase10_7_status()
        return True

    if text_lower in {"/memory-governance-guard-status", "/phase10-7-guard-status"}:
        print_memory_governance_guard_status()
        return True

    if text_lower in {"/memory-governance-test"}:
        print_memory_governance_test()
        return True

    if text_lower.startswith("/memory-governance-test "):
        print_memory_governance_test(text.split(" ", 1)[1])
        return True

    if text_lower in {"/phase10-7-ready", "/p10-7-ready"}:
        print_phase10_7_ready()
        return True

    if text_lower in {"/presence-stability-status", "/phase10-8-status", "/p10-8"}:
        print_presence_stability_status(voice) if text_lower == "/presence-stability-status" else print_phase10_8_status(voice)
        return True

    if text_lower in {"/presence-stability-guard-status", "/phase10-8-guard-status"}:
        print_presence_stability_guard_status(voice)
        return True

    if text_lower in {"/presence-stability-test"}:
        print_presence_stability_test()
        return True

    if text_lower.startswith("/presence-stability-test "):
        print_presence_stability_test(text.split(" ", 1)[1])
        return True

    if text_lower in {"/phase10-8-ready", "/p10-8-ready"}:
        print_phase10_8_ready(voice)
        return True

    if text_lower in {"/social-vision-decouple-status", "/phase10-9-status", "/p10-9"}:
        print_social_vision_decouple_status() if text_lower == "/social-vision-decouple-status" else print_phase10_9_status()
        return True

    if text_lower in {"/social-vision-decouple-guard-status", "/phase10-9-guard-status"}:
        print_social_vision_decouple_guard_status()
        return True

    if text_lower in {"/social-vision-decouple-test"}:
        print_social_vision_decouple_test()
        return True

    if text_lower.startswith("/social-vision-decouple-test "):
        print_social_vision_decouple_test(text.split(" ", 1)[1])
        return True

    if text_lower in {"/phase10-9-ready", "/p10-9-ready"}:
        print_phase10_9_ready()
        return True

    if text_lower in {"/cns-gate-status", "/phase10-10-status", "/p10-10"}:
        print_cns_gate_status(voice) if text_lower == "/cns-gate-status" else print_phase10_10_status(voice)
        return True

    if text_lower in {"/cns-gate-guard-status", "/phase10-10-guard-status"}:
        print_cns_gate_guard_status(voice)
        return True

    if text_lower in {"/cns-gate-test", "/phase10-10-test"}:
        print_cns_gate_test(voice=voice)
        return True

    if text_lower.startswith("/cns-gate-test ") or text_lower.startswith("/phase10-10-test "):
        print_cns_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase10-10-ready", "/p10-10-ready"}:
        print_phase10_10_ready(voice)
        return True

    if text_lower in {"/runtime-stress-status", "/long-session-status", "/phase11-1-status", "/p11-1"}:
        print_runtime_stress_status(voice) if text_lower in {"/runtime-stress-status", "/long-session-status"} else print_phase11_1_status(voice)
        return True

    if text_lower in {"/runtime-stress-guard-status", "/phase11-1-guard-status"}:
        print_runtime_stress_guard_status(voice)
        return True

    if text_lower in {"/runtime-stress-test", "/phase11-1-test"}:
        print_runtime_stress_test(voice=voice)
        return True

    if text_lower.startswith("/runtime-stress-test ") or text_lower.startswith("/phase11-1-test "):
        print_runtime_stress_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase11-1-ready", "/p11-1-ready"}:
        print_phase11_1_ready(voice)
        return True

    if text_lower in {"/trust-calibration-status", "/phase11-2-status", "/p11-2"}:
        print_trust_calibration_status(voice) if text_lower == "/trust-calibration-status" else print_phase11_2_status(voice)
        return True

    if text_lower in {"/trust-calibration-guard-status", "/phase11-2-guard-status"}:
        print_trust_calibration_guard_status(voice)
        return True

    if text_lower in {"/trust-calibration-test", "/phase11-2-test"}:
        print_trust_calibration_test(voice=voice)
        return True

    if text_lower.startswith("/trust-calibration-test ") or text_lower.startswith("/phase11-2-test "):
        print_trust_calibration_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase11-2-ready", "/p11-2-ready"}:
        print_phase11_2_ready(voice)
        return True

    if text_lower in {"/target-lock-status", "/phase11-3-status", "/p11-3"}:
        print_target_lock_status(voice) if text_lower == "/target-lock-status" else print_phase11_3_status(voice)
        return True

    if text_lower in {"/target-lock-guard-status", "/phase11-3-guard-status"}:
        print_target_lock_guard_status(voice)
        return True

    if text_lower in {"/target-lock-test", "/phase11-3-test"}:
        print_target_lock_test(voice=voice)
        return True

    if text_lower.startswith("/target-lock-test ") or text_lower.startswith("/phase11-3-test "):
        print_target_lock_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase11-3-ready", "/p11-3-ready"}:
        print_phase11_3_ready(voice)
        return True

    if text_lower in {"/sandbox-boundary-status", "/kill-switch-status", "/phase11-4-status", "/p11-4"}:
        print_sandbox_boundary_status(voice) if text_lower in {"/sandbox-boundary-status", "/kill-switch-status"} else print_phase11_4_status(voice)
        return True

    if text_lower in {"/sandbox-boundary-guard-status", "/phase11-4-guard-status"}:
        print_sandbox_boundary_guard_status(voice)
        return True

    if text_lower in {"/sandbox-boundary-test", "/kill-switch-test", "/phase11-4-test"}:
        print_sandbox_boundary_test(voice=voice)
        return True

    if (
        text_lower.startswith("/sandbox-boundary-test ")
        or text_lower.startswith("/kill-switch-test ")
        or text_lower.startswith("/phase11-4-test ")
    ):
        print_sandbox_boundary_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase11-4-ready", "/p11-4-ready"}:
        print_phase11_4_ready(voice)
        return True

    if text_lower in {"/executor-rehearsal-status", "/phase11-5-status", "/p11-5"}:
        print_executor_rehearsal_status(voice) if text_lower == "/executor-rehearsal-status" else print_phase11_5_status(voice)
        return True

    if text_lower in {"/executor-rehearsal-guard-status", "/phase11-5-guard-status"}:
        print_executor_rehearsal_guard_status(voice)
        return True

    if text_lower in {"/executor-rehearsal-test", "/phase11-5-test"}:
        print_executor_rehearsal_test(voice=voice)
        return True

    if text_lower.startswith("/executor-rehearsal-test ") or text_lower.startswith("/phase11-5-test "):
        print_executor_rehearsal_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase11-5-ready", "/p11-5-ready"}:
        print_phase11_5_ready(voice)
        return True

    if text_lower in {"/bounded-pilot-status", "/phase11-6-status", "/p11-6"}:
        print_bounded_pilot_status(voice) if text_lower == "/bounded-pilot-status" else print_phase11_6_status(voice)
        return True

    if text_lower in {"/bounded-pilot-guard-status", "/phase11-6-guard-status"}:
        print_bounded_pilot_guard_status(voice)
        return True

    if text_lower in {"/bounded-pilot-test", "/phase11-6-test"}:
        print_bounded_pilot_test(voice=voice)
        return True

    if text_lower.startswith("/bounded-pilot-test ") or text_lower.startswith("/phase11-6-test "):
        print_bounded_pilot_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/bounded-pilot-preview"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            print("🧾 Bounded Pilot Preview")
            print("  Missing: /bounded-pilot-preview <text>")
            return True
        print_bounded_pilot_preview(parts[1], voice=voice)
        return True

    if text_lower in {"/phase11-6-ready", "/p11-6-ready"}:
        print_phase11_6_ready(voice)
        return True

    if text_lower in {"/permission-ledger-status", "/phase11-7-status", "/p11-7"}:
        print_permission_ledger_status(voice) if text_lower == "/permission-ledger-status" else print_phase11_7_status(voice)
        return True

    if text_lower in {"/permission-ledger-guard-status", "/phase11-7-guard-status"}:
        print_permission_ledger_guard_status(voice)
        return True

    if text_lower in {"/permission-ledger-test", "/phase11-7-test"}:
        print_permission_ledger_test(voice=voice)
        return True

    if text_lower.startswith("/permission-ledger-test ") or text_lower.startswith("/phase11-7-test "):
        print_permission_ledger_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/permission-ledger-row"):
        parts = text.split(maxsplit=1)
        print_permission_ledger_row(parts[1] if len(parts) > 1 else "", voice=voice)
        return True

    if text_lower in {"/phase11-7-ready", "/p11-7-ready"}:
        print_phase11_7_ready(voice)
        return True

    if text_lower in {"/session-review-status", "/phase11-8-status", "/p11-8"}:
        print_session_review_status(voice) if text_lower == "/session-review-status" else print_phase11_8_status(voice)
        return True

    if text_lower in {"/session-review-guard-status", "/phase11-8-guard-status"}:
        print_session_review_guard_status(voice)
        return True

    if text_lower in {"/session-review-test", "/phase11-8-test"}:
        print_session_review_test(voice=voice)
        return True

    if text_lower.startswith("/session-review-test ") or text_lower.startswith("/phase11-8-test "):
        print_session_review_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/session-review-preview"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            print("🛡️ Session Review Preview")
            print("  Missing: /session-review-preview <text>")
            return True
        print_session_review_preview(parts[1], voice=voice)
        return True

    if text_lower in {"/phase11-8-ready", "/p11-8-ready"}:
        print_phase11_8_ready(voice)
        return True

    if text_lower in {"/companion-safety-status", "/phase11-9-status", "/p11-9"}:
        print_companion_safety_status(voice) if text_lower == "/companion-safety-status" else print_phase11_9_status(voice)
        return True

    if text_lower in {"/companion-safety-guard-status", "/phase11-9-guard-status"}:
        print_companion_safety_guard_status(voice)
        return True

    if text_lower in {"/companion-safety-test", "/phase11-9-test"}:
        print_companion_safety_test(voice=voice)
        return True

    if text_lower.startswith("/companion-safety-test ") or text_lower.startswith("/phase11-9-test "):
        print_companion_safety_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/companion-safety-check"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            print("🧷 Companion Safety Check")
            print("  Missing: /companion-safety-check <text>")
            return True
        print_companion_safety_check(parts[1], voice=voice)
        return True

    if text_lower in {"/phase11-9-ready", "/p11-9-ready"}:
        print_phase11_9_ready(voice)
        return True

    if text_lower in {"/phase11-gate-status", "/phase11-status", "/phase11-10-status", "/p11", "/p11-10"}:
        print_phase11_gate_status(voice) if text_lower == "/phase11-gate-status" else print_phase11_10_status(voice)
        return True

    if text_lower in {"/phase11-gate-guard-status", "/phase11-10-guard-status"}:
        print_phase11_gate_guard_status(voice)
        return True

    if text_lower in {"/phase11-gate-test", "/phase11-10-test"}:
        print_phase11_gate_test(voice=voice)
        return True

    if text_lower.startswith("/phase11-gate-test ") or text_lower.startswith("/phase11-10-test "):
        print_phase11_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase11-ready", "/phase11-10-ready", "/p11-ready", "/p11-10-ready"}:
        print_phase11_10_ready(voice)
        return True

    if text_lower in {"/attention-state-status", "/phase12-1-status", "/p12-1"}:
        print_attention_state_status(voice) if text_lower == "/attention-state-status" else print_phase12_1_status(voice)
        return True

    if text_lower in {"/attention-state-guard-status", "/phase12-1-guard-status"}:
        print_attention_state_guard_status(voice)
        return True

    if text_lower in {"/attention-state-test", "/phase12-1-test"}:
        print_attention_state_test(voice=voice)
        return True

    if text_lower.startswith("/attention-state-test ") or text_lower.startswith("/phase12-1-test "):
        print_attention_state_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase12-1-ready", "/p12-1-ready"}:
        print_phase12_1_ready(voice)
        return True

    if text_lower in {"/attention-rhythm-status", "/phase12-2-status", "/p12-2"}:
        print_attention_rhythm_status(voice) if text_lower == "/attention-rhythm-status" else print_phase12_2_status(voice)
        return True

    if text_lower in {"/attention-rhythm-guard-status", "/phase12-2-guard-status"}:
        print_attention_rhythm_guard_status(voice)
        return True

    if text_lower in {"/attention-rhythm-test", "/phase12-2-test"}:
        print_attention_rhythm_test(voice=voice)
        return True

    if text_lower.startswith("/attention-rhythm-test ") or text_lower.startswith("/phase12-2-test "):
        print_attention_rhythm_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase12-2-ready", "/p12-2-ready"}:
        print_phase12_2_ready(voice)
        return True

    if text_lower in {"/presence-entropy-status", "/phase12-3-status", "/p12-3"}:
        print_presence_entropy_status(voice) if text_lower == "/presence-entropy-status" else print_phase12_3_status(voice)
        return True

    if text_lower in {"/presence-entropy-guard-status", "/phase12-3-guard-status"}:
        print_presence_entropy_guard_status(voice)
        return True

    if text_lower in {"/presence-entropy-test", "/phase12-3-test"}:
        print_presence_entropy_test(voice=voice)
        return True

    if text_lower.startswith("/presence-entropy-test ") or text_lower.startswith("/phase12-3-test "):
        print_presence_entropy_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase12-3-ready", "/p12-3-ready"}:
        print_phase12_3_ready(voice)
        return True

    if text_lower in {"/attention-memory-status", "/phase12-4-status", "/p12-4"}:
        print_attention_memory_status(voice) if text_lower == "/attention-memory-status" else print_phase12_4_status(voice)
        return True

    if text_lower in {"/attention-memory-guard-status", "/phase12-4-guard-status"}:
        print_attention_memory_guard_status(voice)
        return True

    if text_lower in {"/attention-memory-test", "/phase12-4-test"}:
        print_attention_memory_test(voice=voice)
        return True

    if text_lower.startswith("/attention-memory-test ") or text_lower.startswith("/phase12-4-test "):
        print_attention_memory_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase12-4-ready", "/p12-4-ready"}:
        print_phase12_4_ready(voice)
        return True

    if text_lower in {"/presence-gate-status", "/phase12-status", "/phase12-5-status", "/p12", "/p12-5"}:
        print_presence_gate_status(voice) if text_lower == "/presence-gate-status" else print_phase12_5_status(voice)
        return True

    if text_lower in {"/presence-gate-guard-status", "/phase12-5-guard-status"}:
        print_presence_gate_guard_status(voice)
        return True

    if text_lower in {"/presence-gate-test", "/phase12-5-test"}:
        print_presence_gate_test(voice=voice)
        return True

    if text_lower.startswith("/presence-gate-test ") or text_lower.startswith("/phase12-5-test "):
        print_presence_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase12-ready", "/phase12-5-ready", "/p12-ready", "/p12-5-ready"}:
        print_phase12_ready(voice)
        return True

    if text_lower in {"/memory-v3-status", "/phase13-1-status", "/p13-1"}:
        print_memory_v3_status(voice) if text_lower == "/memory-v3-status" else print_phase13_1_status(voice)
        return True

    if text_lower in {"/memory-v3-guard-status", "/phase13-1-guard-status"}:
        print_memory_v3_guard_status(voice)
        return True

    if text_lower in {"/memory-v3-test", "/phase13-1-test"}:
        print_memory_v3_test(voice=voice)
        return True

    if text_lower.startswith("/memory-v3-test ") or text_lower.startswith("/phase13-1-test "):
        print_memory_v3_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase13-1-ready", "/p13-1-ready"}:
        print_phase13_1_ready(voice)
        return True

    if text_lower in {"/shared-experience-status", "/phase13-2-status", "/p13-2"}:
        print_shared_experience_status(voice) if text_lower == "/shared-experience-status" else print_phase13_2_status(voice)
        return True

    if text_lower in {"/shared-experience-guard-status", "/phase13-2-guard-status"}:
        print_shared_experience_guard_status(voice)
        return True

    if text_lower in {"/shared-experience-test", "/phase13-2-test"}:
        print_shared_experience_test(voice=voice)
        return True

    if text_lower.startswith("/shared-experience-test ") or text_lower.startswith("/phase13-2-test "):
        print_shared_experience_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase13-2-ready", "/p13-2-ready"}:
        print_phase13_2_ready(voice)
        return True

    if text_lower in {"/memory-conflict-status", "/phase13-3-status", "/p13-3"}:
        print_memory_conflict_status(voice) if text_lower == "/memory-conflict-status" else print_phase13_3_status(voice)
        return True

    if text_lower in {"/memory-conflict-guard-status", "/phase13-3-guard-status"}:
        print_memory_conflict_guard_status(voice)
        return True

    if text_lower in {"/memory-conflict-test", "/phase13-3-test"}:
        print_memory_conflict_test(voice=voice)
        return True

    if text_lower.startswith("/memory-conflict-test ") or text_lower.startswith("/phase13-3-test "):
        print_memory_conflict_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase13-3-ready", "/p13-3-ready"}:
        print_phase13_3_ready(voice)
        return True

    if text_lower in {"/memory-decay-status", "/phase13-4-status", "/p13-4"}:
        print_memory_decay_status(voice) if text_lower == "/memory-decay-status" else print_phase13_4_status(voice)
        return True

    if text_lower in {"/memory-decay-guard-status", "/phase13-4-guard-status"}:
        print_memory_decay_guard_status(voice)
        return True

    if text_lower in {"/memory-decay-test", "/phase13-4-test"}:
        print_memory_decay_test(voice=voice)
        return True

    if text_lower.startswith("/memory-decay-test ") or text_lower.startswith("/phase13-4-test "):
        print_memory_decay_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase13-4-ready", "/p13-4-ready"}:
        print_phase13_4_ready(voice)
        return True

    if text_lower in {"/memory-gate-status", "/phase13-status", "/phase13-5-status", "/p13", "/p13-5"}:
        print_memory_gate_status(voice) if text_lower == "/memory-gate-status" else print_phase13_5_status(voice)
        return True

    if text_lower in {"/memory-gate-guard-status", "/phase13-5-guard-status"}:
        print_memory_gate_guard_status(voice)
        return True

    if text_lower in {"/memory-gate-test", "/phase13-5-test"}:
        print_memory_gate_test(voice=voice)
        return True

    if text_lower.startswith("/memory-gate-test ") or text_lower.startswith("/phase13-5-test "):
        print_memory_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase13-ready", "/phase13-5-ready", "/p13-ready", "/p13-5-ready"}:
        print_phase13_ready(voice)
        return True

    if text_lower in {"/dialogue-energy-status", "/phase14-1-status", "/p14-1"}:
        print_dialogue_energy_status(voice) if text_lower == "/dialogue-energy-status" else print_phase14_1_status(voice)
        return True

    if text_lower in {"/dialogue-energy-guard-status", "/phase14-1-guard-status"}:
        print_dialogue_energy_guard_status(voice)
        return True

    if text_lower in {"/dialogue-energy-test", "/phase14-1-test"}:
        print_dialogue_energy_test(voice=voice)
        return True

    if text_lower.startswith("/dialogue-energy-test ") or text_lower.startswith("/phase14-1-test "):
        print_dialogue_energy_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase14-1-ready", "/p14-1-ready"}:
        print_phase14_1_ready(voice)
        return True

    if text_lower in {"/response-shape-status", "/phase14-2-status", "/p14-2"}:
        print_response_shape_status(voice) if text_lower == "/response-shape-status" else print_phase14_2_status(voice)
        return True

    if text_lower in {"/response-shape-guard-status", "/phase14-2-guard-status"}:
        print_response_shape_guard_status(voice)
        return True

    if text_lower in {"/response-shape-test", "/phase14-2-test"}:
        print_response_shape_test(voice=voice)
        return True

    if text_lower.startswith("/response-shape-test ") or text_lower.startswith("/phase14-2-test "):
        print_response_shape_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase14-2-ready", "/p14-2-ready"}:
        print_phase14_2_ready(voice)
        return True

    if text_lower in {"/dialogue-drift-status", "/phase14-3-status", "/p14-3"}:
        print_dialogue_drift_status(voice) if text_lower == "/dialogue-drift-status" else print_phase14_3_status(voice)
        return True

    if text_lower in {"/dialogue-drift-guard-status", "/phase14-3-guard-status"}:
        print_dialogue_drift_guard_status(voice)
        return True

    if text_lower in {"/dialogue-drift-test", "/phase14-3-test"}:
        print_dialogue_drift_test(voice=voice)
        return True

    if text_lower.startswith("/dialogue-drift-test ") or text_lower.startswith("/phase14-3-test "):
        print_dialogue_drift_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower == "/dialogue-drift-check":
        print("🧯 Dialogue Drift Check")
        print("  Missing: /dialogue-drift-check <reply candidate>")
        print("  Optional shape prefix: /dialogue-drift-check shape=soft_short | <reply>")
        return True

    if text_lower.startswith("/dialogue-drift-check "):
        raw = text.split(" ", 1)[1].strip()
        shape = None
        if raw.lower().startswith("shape=") and "|" in raw:
            prefix, raw = raw.split("|", 1)
            shape = prefix.replace("shape=", "", 1).strip()
            raw = raw.strip()
        print_dialogue_drift_check(raw, shape=shape, voice=voice)
        return True

    if text_lower in {"/phase14-3-ready", "/p14-3-ready"}:
        print_phase14_3_ready(voice)
        return True

    if text_lower in {"/conversation-rhythm-status", "/phase14-4-status", "/p14-4"}:
        print_conversation_rhythm_status(voice) if text_lower == "/conversation-rhythm-status" else print_phase14_4_status(voice)
        return True

    if text_lower in {"/conversation-rhythm-guard-status", "/phase14-4-guard-status"}:
        print_conversation_rhythm_guard_status(voice)
        return True

    if text_lower in {"/conversation-rhythm-test", "/phase14-4-test"}:
        print_conversation_rhythm_test(voice=voice)
        return True

    if text_lower.startswith("/conversation-rhythm-test ") or text_lower.startswith("/phase14-4-test "):
        print_conversation_rhythm_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower == "/conversation-rhythm-check":
        print("🫀 Conversation Rhythm Check")
        print("  Missing: /conversation-rhythm-check <text>")
        print("  Optional: /conversation-rhythm-check attention=deep_work | text | candidate")
        return True

    if text_lower.startswith("/conversation-rhythm-check "):
        raw = text.split(" ", 1)[1].strip()
        attention = None
        candidate = None
        if raw.lower().startswith("attention=") and "|" in raw:
            prefix, rest = raw.split("|", 1)
            attention = prefix.replace("attention=", "", 1).strip()
            parts = [part.strip() for part in rest.split("|", 1)]
            raw = parts[0]
            candidate = parts[1] if len(parts) > 1 else None
        print_conversation_rhythm_check(raw, attention_state=attention, candidate_text=candidate, voice=voice)
        return True

    if text_lower in {"/phase14-4-ready", "/p14-4-ready"}:
        print_phase14_4_ready(voice)
        return True

    if text_lower in {"/dialogue-gate-status", "/phase14-status", "/phase14-5-status", "/p14", "/p14-5"}:
        print_dialogue_gate_status(voice) if text_lower == "/dialogue-gate-status" else print_phase14_5_status(voice)
        return True

    if text_lower in {"/dialogue-gate-guard-status", "/phase14-5-guard-status"}:
        print_dialogue_gate_guard_status(voice)
        return True

    if text_lower in {"/dialogue-gate-test", "/phase14-5-test"}:
        print_dialogue_gate_test(voice=voice)
        return True

    if text_lower.startswith("/dialogue-gate-test ") or text_lower.startswith("/phase14-5-test "):
        print_dialogue_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase14-ready", "/phase14-5-ready", "/p14-ready", "/p14-5-ready"}:
        print_phase14_ready(voice)
        return True

    if text_lower in {"/daily-frame-status", "/session-atmosphere-status", "/phase15-1-status", "/p15-1"}:
        print_daily_frame_status(voice) if text_lower in {"/daily-frame-status", "/session-atmosphere-status"} else print_phase15_1_status(voice)
        return True

    if text_lower in {"/daily-frame-guard-status", "/phase15-1-guard-status"}:
        print_daily_frame_guard_status(voice)
        return True

    if text_lower in {"/daily-frame-test", "/session-atmosphere-test", "/phase15-1-test"}:
        print_daily_frame_test(voice=voice)
        return True

    if text_lower.startswith("/daily-frame-test ") or text_lower.startswith("/session-atmosphere-test ") or text_lower.startswith("/phase15-1-test "):
        print_daily_frame_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase15-1-ready", "/p15-1-ready"}:
        print_phase15_1_ready(voice)
        return True

    if text_lower in {"/day-continuity-status", "/continuity-candidate-status", "/phase15-2-status", "/p15-2"}:
        print_day_continuity_status(voice) if text_lower in {"/day-continuity-status", "/continuity-candidate-status"} else print_phase15_2_status(voice)
        return True

    if text_lower in {"/day-continuity-guard-status", "/phase15-2-guard-status"}:
        print_day_continuity_guard_status(voice)
        return True

    if text_lower in {"/day-continuity-test", "/continuity-candidate-test", "/phase15-2-test"}:
        print_day_continuity_test(voice=voice)
        return True

    if text_lower.startswith("/day-continuity-test ") or text_lower.startswith("/continuity-candidate-test ") or text_lower.startswith("/phase15-2-test "):
        print_day_continuity_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase15-2-ready", "/p15-2-ready"}:
        print_phase15_2_ready(voice)
        return True

    if text_lower in {"/habit-candidate-status", "/companion-habit-status", "/phase15-3-status", "/p15-3"}:
        print_habit_candidate_status(voice) if text_lower in {"/habit-candidate-status", "/companion-habit-status"} else print_phase15_3_status(voice)
        return True

    if text_lower in {"/habit-candidate-guard-status", "/phase15-3-guard-status"}:
        print_habit_candidate_guard_status(voice)
        return True

    if text_lower in {"/habit-candidate-test", "/companion-habit-test", "/phase15-3-test"}:
        print_habit_candidate_test(voice=voice)
        return True

    if text_lower.startswith("/habit-candidate-test ") or text_lower.startswith("/companion-habit-test ") or text_lower.startswith("/phase15-3-test "):
        print_habit_candidate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase15-3-ready", "/p15-3-ready"}:
        print_phase15_3_ready(voice)
        return True

    if text_lower in {"/recovery-continuity-status", "/phase15-4-status", "/p15-4"}:
        print_recovery_continuity_status(voice) if text_lower == "/recovery-continuity-status" else print_phase15_4_status(voice)
        return True

    if text_lower in {"/recovery-continuity-guard-status", "/phase15-4-guard-status"}:
        print_recovery_continuity_guard_status(voice)
        return True

    if text_lower in {"/recovery-continuity-test", "/phase15-4-test"}:
        print_recovery_continuity_test(voice=voice)
        return True

    if text_lower.startswith("/recovery-continuity-test ") or text_lower.startswith("/phase15-4-test "):
        print_recovery_continuity_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase15-4-ready", "/p15-4-ready"}:
        print_phase15_4_ready(voice)
        return True

    if text_lower in {"/daily-loop-status", "/phase15-status", "/phase15-5-status", "/p15", "/p15-5"}:
        print_daily_loop_status(voice) if text_lower == "/daily-loop-status" else print_phase15_5_status(voice)
        return True

    if text_lower in {"/daily-loop-guard-status", "/phase15-5-guard-status"}:
        print_daily_loop_guard_status(voice)
        return True

    if text_lower in {"/daily-loop-test", "/phase15-5-test"}:
        print_daily_loop_test(voice=voice)
        return True

    if text_lower.startswith("/daily-loop-test ") or text_lower.startswith("/phase15-5-test "):
        print_daily_loop_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase15-ready", "/phase15-5-ready", "/p15-ready", "/p15-5-ready"}:
        print_phase15_ready(voice)
        return True

    if text_lower in {"/reflective-state-status", "/reflective-presence-status", "/phase16-1-status", "/p16-1"}:
        print_reflective_state_status(voice) if text_lower in {"/reflective-state-status", "/reflective-presence-status"} else print_phase16_1_status(voice)
        return True

    if text_lower in {"/reflective-state-guard-status", "/phase16-1-guard-status"}:
        print_reflective_state_guard_status(voice)
        return True

    if text_lower in {"/reflective-state-test", "/phase16-1-test"}:
        print_reflective_state_test(voice=voice)
        return True

    if text_lower.startswith("/reflective-state-test ") or text_lower.startswith("/phase16-1-test "):
        print_reflective_state_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase16-1-ready", "/p16-1-ready"}:
        print_phase16_1_ready(voice)
        return True

    if text_lower in {"/grounded-reflection-status", "/phase16-2-status", "/p16-2"}:
        print_grounded_reflection_status(voice) if text_lower == "/grounded-reflection-status" else print_phase16_2_status(voice)
        return True

    if text_lower in {"/grounded-reflection-guard-status", "/phase16-2-guard-status"}:
        print_grounded_reflection_guard_status(voice)
        return True

    if text_lower in {"/grounded-reflection-test", "/phase16-2-test"}:
        print_grounded_reflection_test(voice=voice)
        return True

    if text_lower.startswith("/grounded-reflection-test ") or text_lower.startswith("/phase16-2-test "):
        print_grounded_reflection_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase16-2-ready", "/p16-2-ready"}:
        print_phase16_2_ready(voice)
        return True

    if text_lower in {"/shared-recall-status", "/phase16-3-status", "/p16-3"}:
        print_shared_recall_status(voice) if text_lower == "/shared-recall-status" else print_phase16_3_status(voice)
        return True

    if text_lower in {"/shared-recall-guard-status", "/phase16-3-guard-status"}:
        print_shared_recall_guard_status(voice)
        return True

    if text_lower in {"/shared-recall-test", "/phase16-3-test"}:
        print_shared_recall_test(voice=voice)
        return True

    if text_lower.startswith("/shared-recall-test ") or text_lower.startswith("/phase16-3-test "):
        print_shared_recall_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/shared-recall-preview"):
        parts = text.split(maxsplit=1)
        print_shared_recall_preview(parts[1] if len(parts) > 1 else "", voice=voice)
        return True

    if text_lower in {"/phase16-3-ready", "/p16-3-ready"}:
        print_phase16_3_ready(voice)
        return True

    if text_lower in {"/reflection-safety-status", "/anti-lore-status", "/phase16-4-status", "/p16-4"}:
        print_reflection_safety_status(voice) if text_lower in {"/reflection-safety-status", "/anti-lore-status"} else print_phase16_4_status(voice)
        return True

    if text_lower in {"/reflection-safety-guard-status", "/phase16-4-guard-status"}:
        print_reflection_safety_guard_status(voice)
        return True

    if text_lower in {"/reflection-safety-test", "/anti-lore-test", "/phase16-4-test"}:
        print_reflection_safety_test(voice=voice)
        return True

    if text_lower.startswith("/reflection-safety-test ") or text_lower.startswith("/anti-lore-test ") or text_lower.startswith("/phase16-4-test "):
        print_reflection_safety_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/reflection-safety-check"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            print("🧷 Reflection Safety Check")
            print("  Missing: /reflection-safety-check <text>")
            return True
        print_reflection_safety_check(parts[1], voice=voice)
        return True

    if text_lower in {"/phase16-4-ready", "/p16-4-ready"}:
        print_phase16_4_ready(voice)
        return True

    if text_lower in {"/reflective-gate-status", "/phase16-status", "/phase16-5-status", "/p16", "/p16-5"}:
        print_reflective_gate_status(voice) if text_lower == "/reflective-gate-status" else print_phase16_5_status(voice)
        return True

    if text_lower in {"/reflective-gate-guard-status", "/phase16-5-guard-status"}:
        print_reflective_gate_guard_status(voice)
        return True

    if text_lower in {"/reflective-gate-test", "/phase16-5-test"}:
        print_reflective_gate_test(voice=voice)
        return True

    if text_lower.startswith("/reflective-gate-test ") or text_lower.startswith("/phase16-5-test "):
        print_reflective_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase16-ready", "/phase16-5-ready", "/p16-ready", "/p16-5-ready"}:
        print_phase16_ready(voice)
        return True

    if text_lower in {"/output-candidate-status", "/phase17-1-status", "/p17-1"}:
        print_output_candidate_status(voice) if text_lower == "/output-candidate-status" else print_phase17_1_status(voice)
        return True

    if text_lower in {"/output-candidate-guard-status", "/phase17-1-guard-status"}:
        print_output_candidate_guard_status(voice)
        return True

    if text_lower in {"/output-candidate-test", "/phase17-1-test"}:
        print_output_candidate_test(voice=voice)
        return True

    if text_lower.startswith("/output-candidate-test ") or text_lower.startswith("/phase17-1-test "):
        print_output_candidate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/output-candidate-preview"):
        parts = text.split(maxsplit=1)
        print_output_candidate_preview(parts[1] if len(parts) > 1 else "", voice=voice)
        return True

    if text_lower in {"/phase17-1-ready", "/p17-1-ready"}:
        print_phase17_1_ready(voice)
        return True

    if text_lower in {"/reflection-injection-status", "/phase17-2-status", "/p17-2"}:
        print_reflection_injection_status(voice) if text_lower == "/reflection-injection-status" else print_phase17_2_status(voice)
        return True

    if text_lower in {"/reflection-injection-guard-status", "/phase17-2-guard-status"}:
        print_reflection_injection_guard_status(voice)
        return True

    if text_lower in {"/reflection-injection-test", "/phase17-2-test"}:
        print_reflection_injection_test(voice=voice)
        return True

    if text_lower.startswith("/reflection-injection-test ") or text_lower.startswith("/phase17-2-test "):
        print_reflection_injection_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/reflection-injection-preview"):
        parts = text.split(maxsplit=1)
        print_reflection_injection_preview(parts[1] if len(parts) > 1 else "", voice=voice)
        return True

    if text_lower in {"/phase17-2-ready", "/p17-2-ready"}:
        print_phase17_2_ready(voice)
        return True

    if text_lower in {"/silence-hold-status", "/phase17-3-status", "/p17-3"}:
        print_silence_hold_status(voice) if text_lower == "/silence-hold-status" else print_phase17_3_status(voice)
        return True

    if text_lower in {"/silence-hold-guard-status", "/phase17-3-guard-status"}:
        print_silence_hold_guard_status(voice)
        return True

    if text_lower in {"/silence-hold-test", "/phase17-3-test"}:
        print_silence_hold_test(voice=voice)
        return True

    if text_lower.startswith("/silence-hold-test ") or text_lower.startswith("/phase17-3-test "):
        print_silence_hold_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/silence-hold-preview"):
        parts = text.split(maxsplit=1)
        print_silence_hold_preview(parts[1] if len(parts) > 1 else "", voice=voice)
        return True

    if text_lower in {"/phase17-3-ready", "/p17-3-ready"}:
        print_phase17_3_ready(voice)
        return True

    if text_lower in {"/output-gate-status", "/phase17-status", "/phase17-4-status", "/p17", "/p17-4"}:
        print_output_gate_status(voice) if text_lower == "/output-gate-status" else print_phase17_4_status(voice)
        return True

    if text_lower in {"/output-gate-guard-status", "/phase17-4-guard-status"}:
        print_output_gate_guard_status(voice)
        return True

    if text_lower in {"/output-gate-test", "/phase17-4-test"}:
        print_output_gate_test(voice=voice)
        return True

    if text_lower.startswith("/output-gate-test ") or text_lower.startswith("/phase17-4-test "):
        print_output_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase17-ready", "/phase17-4-ready", "/p17-ready", "/p17-4-ready"}:
        print_phase17_ready(voice)
        return True

    if text_lower in {"/companion-integration-status", "/phase18-1-status", "/p18-1"}:
        print_companion_integration_status(voice) if text_lower == "/companion-integration-status" else print_phase18_1_status(voice)
        return True

    if text_lower in {"/companion-integration-guard-status", "/phase18-1-guard-status"}:
        print_companion_integration_guard_status(voice)
        return True

    if text_lower in {"/companion-integration-test", "/phase18-1-test"}:
        print_companion_integration_test(voice=voice)
        return True

    if text_lower.startswith("/companion-integration-test ") or text_lower.startswith("/phase18-1-test "):
        print_companion_integration_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase18-1-ready", "/p18-1-ready"}:
        print_phase18_1_ready(voice)
        return True

    if text_lower in {"/companion-consistency-status", "/phase18-2-status", "/p18-2"}:
        print_companion_consistency_status(voice) if text_lower == "/companion-consistency-status" else print_phase18_2_status(voice)
        return True

    if text_lower in {"/companion-consistency-guard-status", "/phase18-2-guard-status"}:
        print_companion_consistency_guard_status(voice)
        return True

    if text_lower in {"/companion-consistency-test", "/phase18-2-test"}:
        print_companion_consistency_test(voice=voice)
        return True

    if text_lower.startswith("/companion-consistency-test ") or text_lower.startswith("/phase18-2-test "):
        print_companion_consistency_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase18-2-ready", "/p18-2-ready"}:
        print_phase18_2_ready(voice)
        return True

    if text_lower in {"/companion-response-status", "/phase18-3-status", "/p18-3"}:
        print_companion_response_status(voice) if text_lower == "/companion-response-status" else print_phase18_3_status(voice)
        return True

    if text_lower in {"/companion-response-guard-status", "/phase18-3-guard-status"}:
        print_companion_response_guard_status(voice)
        return True

    if text_lower in {"/companion-response-test", "/phase18-3-test"}:
        print_companion_response_test(voice=voice)
        return True

    if text_lower.startswith("/companion-response-test ") or text_lower.startswith("/phase18-3-test "):
        print_companion_response_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower.startswith("/companion-response-preview"):
        parts = text.split(maxsplit=1)
        print_companion_response_preview(parts[1] if len(parts) > 1 else "", voice=voice)
        return True

    if text_lower in {"/phase18-3-ready", "/p18-3-ready"}:
        print_phase18_3_ready(voice)
        return True

    if text_lower in {"/companion-live-status", "/phase18-status", "/phase18-4-status", "/p18", "/p18-4"}:
        print_companion_live_status(voice) if text_lower == "/companion-live-status" else print_phase18_4_status(voice)
        return True

    if text_lower in {"/companion-live-guard-status", "/phase18-4-guard-status"}:
        print_companion_live_guard_status(voice)
        return True

    if text_lower in {"/companion-live-test", "/phase18-4-test"}:
        print_companion_live_test(voice=voice)
        return True

    if text_lower.startswith("/companion-live-test ") or text_lower.startswith("/phase18-4-test "):
        print_companion_live_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase18-ready", "/phase18-4-ready", "/p18-ready", "/p18-4-ready"}:
        print_phase18_ready(voice)
        return True

    if text_lower in {"/nana-boundary-status", "/phase81-status", "/p81"}:
        print_nana_boundary_status(vts, voice) if text_lower == "/nana-boundary-status" else print_phase81_status(vts, voice)
        return True

    if text_lower in {"/nana-boundary-guard-status", "/phase81-guard-status"}:
        print_nana_boundary_guard_status(vts, voice)
        return True

    if text_lower in {"/nana-boundary-test", "/phase81-test"}:
        print_nana_boundary_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/nana-boundary-test ") or text_lower.startswith("/phase81-test "):
        print_nana_boundary_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase81-ready", "/p81-ready"}:
        print_phase81_ready(vts, voice)
        return True

    if text_lower in {"/nana-stream-status", "/phase82-status", "/p82"}:
        print_nana_stream_status(vts, voice) if text_lower == "/nana-stream-status" else print_phase82_status(vts, voice)
        return True

    if text_lower in {"/nana-stream-guard-status", "/phase82-guard-status"}:
        print_nana_stream_guard_status(vts, voice)
        return True

    if text_lower in {"/nana-stream-test", "/phase82-test"}:
        print_nana_stream_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/nana-stream-test ") or text_lower.startswith("/phase82-test "):
        print_nana_stream_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower.startswith("/nana-stream-mode"):
        parts = text.split(maxsplit=1)
        if len(parts) == 1:
            print("🎙️ Nana Stream Mode")
            print(f"  Mode: {cli_globals.PHASE82_RUNTIME_STREAM_MODE}")
            print("  Options: off | light | host")
        else:
            requested = phase82_normalize_stream_mode(parts[1])
            cli_globals.PHASE82_RUNTIME_STREAM_MODE = requested
            print("🎙️ Nana Stream Mode")
            print(f"  Mode: {cli_globals.PHASE82_RUNTIME_STREAM_MODE}")
            print("  Note: runtime only; không tự nói/không ghi memory.")
        return True

    if text_lower in {"/phase82-ready", "/p82-ready"}:
        print_phase82_ready(vts, voice)
        return True

    if text_lower in {"/nana-priority-status", "/phase83-status", "/p83"}:
        print_nana_priority_status(vts, voice) if text_lower == "/nana-priority-status" else print_phase83_status(vts, voice)
        return True

    if text_lower in {"/nana-priority-guard-status", "/phase83-guard-status"}:
        print_nana_priority_guard_status(vts, voice)
        return True

    if text_lower in {"/nana-priority-test", "/phase83-test"}:
        print_nana_priority_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/nana-priority-test ") or text_lower.startswith("/phase83-test "):
        print_nana_priority_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase83-ready", "/p83-ready"}:
        print_phase83_ready(vts, voice)
        return True

    if text_lower in {"/nana-runtime-state-status", "/phase84-status", "/p84"}:
        print_nana_runtime_state_status(vts, voice) if text_lower == "/nana-runtime-state-status" else print_phase84_status(vts, voice)
        return True

    if text_lower in {"/nana-runtime-state-guard-status", "/phase84-guard-status"}:
        print_nana_runtime_state_guard_status(vts, voice)
        return True

    if text_lower in {"/nana-runtime-state-test", "/phase84-test"}:
        print_nana_runtime_state_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/nana-runtime-state-test ") or text_lower.startswith("/phase84-test "):
        print_nana_runtime_state_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase84-ready", "/p84-ready"}:
        print_phase84_ready(vts, voice)
        return True

    if text_lower in {"/nana-policy-map-status", "/phase85-status", "/p85"}:
        print_nana_policy_map_status(vts, voice) if text_lower == "/nana-policy-map-status" else print_phase85_status(vts, voice)
        return True

    if text_lower in {"/nana-policy-map-guard-status", "/phase85-guard-status"}:
        print_nana_policy_map_guard_status(vts, voice)
        return True

    if text_lower in {"/nana-policy-map-test", "/phase85-test"}:
        print_nana_policy_map_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/nana-policy-map-test ") or text_lower.startswith("/phase85-test "):
        print_nana_policy_map_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/nana-level5-map"}:
        print_nana_level5_map(vts, voice)
        return True

    if text_lower in {"/phase85-ready", "/p85-ready"}:
        print_phase85_ready(vts, voice)
        return True


    return False
