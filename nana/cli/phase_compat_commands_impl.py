"""Phase/voice/stream compatibility command router for Nana CLI.

This keeps legacy phase shells and late voice/stream phase routes out of the
main chat dispatcher without changing their command behavior.
"""

from __future__ import annotations

from importlib import import_module


def _ensure_browser_snapshot(loop, **kwargs):
    return import_module("nana.runtime.browser_refresh").ensure_browser_snapshot(loop, **kwargs)


from nana.cli.legacy_archive import (
    print_controlled_stream_gate_guard_status,
    print_controlled_stream_gate_status,
    print_controlled_stream_gate_test,
    print_controlled_stream_pilot_guard_status,
    print_controlled_stream_pilot_status,
    print_controlled_stream_pilot_test,
    print_direct_voice_pilot_guard_status,
    print_direct_voice_pilot_test,
    print_final_phase_guard_status,
    print_final_phase_ready,
    print_final_phase_status,
    print_final_phase_test,
    print_guarded_stream_call_guard_status,
    print_guarded_stream_call_status,
    print_guarded_stream_call_test,
    print_guarded_voice_dispatch_guard_status,
    print_guarded_voice_dispatch_test,
    print_live_path_replacement_guard_status,
    print_live_path_replacement_status,
    print_live_path_replacement_test,
    print_live_stream_measurement_guard_status,
    print_live_stream_measurement_status,
    print_live_stream_measurement_test,
    print_live_voice_control_guard_status,
    print_live_voice_control_status,
    print_live_voice_control_test,
    print_live_voice_gate_guard_status,
    print_live_voice_gate_status,
    print_live_voice_gate_test,
    print_phase21_1_status,
    print_phase21_2_status,
    print_phase21_3_status,
    print_phase21_4_status,
    print_phase22_1_ready,
    print_phase22_2_ready,
    print_phase22_3_ready,
    print_phase22_4_ready,
    print_phase23_1_ready,
    print_phase23_2_ready,
    print_phase23_3_ready,
    print_phase23_4_ready,
    print_phase24_1_ready,
    print_phase24_2_ready,
    print_phase24_3_ready,
    print_phase24_4_ready,
    print_phase25_1_ready,
    print_phase25_2_ready,
    print_phase5_status,
    print_phase6_ready,
    print_phase6_status,
    print_stream_pilot_control_guard_status,
    print_stream_pilot_control_status,
    print_stream_pilot_control_test,
    print_stream_pilot_enable_guard_status,
    print_stream_pilot_enable_status,
    print_stream_pilot_enable_test,
    print_stream_rollback_guard_status,
    print_stream_rollback_status,
    print_stream_rollback_test,
    print_voice_engine_impl_guard_status,
    print_voice_engine_impl_status,
    print_voice_engine_impl_test,
    print_voice_engine_patch_guard_status,
    print_voice_engine_patch_status,
    print_voice_engine_patch_test,
    print_voice_latency_baseline_guard_status,
    print_voice_latency_baseline_status,
    print_voice_latency_baseline_test,
    print_voice_latency_design_guard_status,
    print_voice_latency_design_status,
    print_voice_latency_design_test,
    print_voice_latency_gate_guard_status,
    print_voice_latency_gate_status,
    print_voice_latency_gate_test,
    print_voice_stream_gate_guard_status,
    print_voice_stream_gate_status,
    print_voice_stream_gate_test,
    print_voice_stream_safety_guard_status,
    print_voice_stream_safety_status,
    print_voice_stream_safety_test,
    print_voice_streaming_decision_guard_status,
    print_voice_streaming_decision_status,
    print_voice_streaming_decision_test,
    print_voice_streaming_dry_run_guard_status,
    print_voice_streaming_dry_run_status,
    print_voice_streaming_dry_run_test,
    print_voice_telemetry_guard_status,
    print_voice_telemetry_status,
    print_voice_telemetry_test,
)
from nana.phases import (
    print_direct_voice_pilot_status,
    print_expression_dispatch_guard_status,
    print_expression_dispatch_status,
    print_expression_dispatch_test,
    print_guarded_voice_dispatch_status,
    print_live_reply_gate_guard_status,
    print_live_reply_gate_status,
    print_live_reply_gate_test,
    print_live_reply_guard_status,
    print_live_reply_status,
    print_live_reply_test,
    print_live_speech_safety_guard_status,
    print_live_speech_safety_status,
    print_live_speech_safety_test,
    print_phase19_1_ready,
    print_phase19_1_status,
    print_phase19_2_ready,
    print_phase19_2_status,
    print_phase19_3_ready,
    print_phase19_3_status,
    print_phase19_4_ready,
    print_phase19_4_status,
    print_phase19_5_status,
    print_phase19_ready,
    print_phase20_1_ready,
    print_phase20_1_status,
    print_phase20_2_ready,
    print_phase20_2_status,
    print_phase20_3_ready,
    print_phase20_3_status,
    print_phase20_4_ready,
    print_phase20_4_status,
    print_phase20_5_status,
    print_phase20_ready,
    print_phase21_1_ready,
    print_phase21_2_ready,
    print_phase21_3_ready,
    print_phase21_4_ready,
    print_phase21_5_status,
    print_phase21_ready,
    print_phase22_1_status,
    print_phase22_2_status,
    print_phase22_3_status,
    print_phase22_4_status,
    print_phase22_5_status,
    print_phase22_ready,
    print_phase23_1_status,
    print_phase23_2_status,
    print_phase23_3_status,
    print_phase23_4_status,
    print_phase23_5_status,
    print_phase23_ready,
    print_phase24_1_status,
    print_phase24_2_status,
    print_phase24_3_status,
    print_phase24_4_status,
    print_phase24_5_status,
    print_phase24_ready,
    print_phase25_1_status,
    print_phase25_2_status,
    print_reply_bridge_guard_status,
    print_reply_bridge_status,
    print_reply_bridge_test,
    print_reply_cooldown_guard_status,
    print_reply_cooldown_status,
    print_reply_cooldown_test,
    print_speech_dispatch_guard_status,
    print_speech_dispatch_status,
    print_speech_dispatch_test,
    print_voice_binding_guard_status,
    print_voice_binding_status,
    print_voice_binding_test,
    print_voice_dispatch_guard_status,
    print_voice_dispatch_status,
    print_voice_dispatch_test,
    print_voice_gate_guard_status,
    print_voice_gate_status,
    print_voice_gate_test,
)


async def handle_phase_compat_command(loop, vts, voice, text: str, text_lower: str | None = None) -> bool:
    text_lower = text_lower or text.lower()
    if text_lower in {"/phase5-status", "/p5"}:
        await _ensure_browser_snapshot(loop, reason="phase5_status_cache")
        print_phase5_status()
        return True

    if text_lower in {"/phase6-status", "/p6"}:
        await _ensure_browser_snapshot(loop, reason="phase6_status_cache")
        print_phase6_status()
        return True

    if text_lower in {"/phase6-ready", "/p6-ready"}:
        await _ensure_browser_snapshot(loop, reason="phase6_ready_cache")
        print_phase6_ready()
        return True


    if text_lower in {"/live-reply-status", "/phase19-1-status", "/p19-1"}:
        print_live_reply_status(voice) if text_lower == "/live-reply-status" else print_phase19_1_status(voice)
        return True

    if text_lower in {"/live-reply-guard-status", "/phase19-1-guard-status"}:
        print_live_reply_guard_status(voice)
        return True

    if text_lower in {"/live-reply-test", "/phase19-1-test"}:
        print_live_reply_test(voice=voice)
        return True

    if text_lower.startswith("/live-reply-test ") or text_lower.startswith("/phase19-1-test "):
        print_live_reply_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase19-1-ready", "/p19-1-ready"}:
        print_phase19_1_ready(voice)
        return True

    if text_lower in {"/speech-dispatch-status", "/phase19-2-status", "/p19-2"}:
        print_speech_dispatch_status(voice) if text_lower == "/speech-dispatch-status" else print_phase19_2_status(voice)
        return True

    if text_lower in {"/speech-dispatch-guard-status", "/phase19-2-guard-status"}:
        print_speech_dispatch_guard_status(voice)
        return True

    if text_lower in {"/speech-dispatch-test", "/phase19-2-test"}:
        print_speech_dispatch_test(voice=voice)
        return True

    if text_lower.startswith("/speech-dispatch-test ") or text_lower.startswith("/phase19-2-test "):
        print_speech_dispatch_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase19-2-ready", "/p19-2-ready"}:
        print_phase19_2_ready(voice)
        return True

    if text_lower in {"/reply-bridge-status", "/phase19-3-status", "/p19-3"}:
        print_reply_bridge_status(voice) if text_lower == "/reply-bridge-status" else print_phase19_3_status(voice)
        return True

    if text_lower in {"/reply-bridge-guard-status", "/phase19-3-guard-status"}:
        print_reply_bridge_guard_status(voice)
        return True

    if text_lower in {"/reply-bridge-test", "/phase19-3-test"}:
        print_reply_bridge_test(voice=voice)
        return True

    if text_lower.startswith("/reply-bridge-test ") or text_lower.startswith("/phase19-3-test "):
        print_reply_bridge_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase19-3-ready", "/p19-3-ready"}:
        print_phase19_3_ready(voice)
        return True

    if text_lower in {"/reply-cooldown-status", "/phase19-4-status", "/p19-4"}:
        print_reply_cooldown_status(voice) if text_lower == "/reply-cooldown-status" else print_phase19_4_status(voice)
        return True

    if text_lower in {"/reply-cooldown-guard-status", "/phase19-4-guard-status"}:
        print_reply_cooldown_guard_status(voice)
        return True

    if text_lower in {"/reply-cooldown-test", "/phase19-4-test"}:
        print_reply_cooldown_test(voice=voice)
        return True

    if text_lower.startswith("/reply-cooldown-test ") or text_lower.startswith("/phase19-4-test "):
        print_reply_cooldown_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase19-4-ready", "/p19-4-ready"}:
        print_phase19_4_ready(voice)
        return True

    if text_lower in {"/live-reply-gate-status", "/phase19-status", "/phase19-5-status", "/p19", "/p19-5"}:
        print_live_reply_gate_status(voice) if text_lower == "/live-reply-gate-status" else print_phase19_5_status(voice)
        return True

    if text_lower in {"/live-reply-gate-guard-status", "/phase19-5-guard-status"}:
        print_live_reply_gate_guard_status(voice)
        return True

    if text_lower in {"/live-reply-gate-test", "/phase19-5-test"}:
        print_live_reply_gate_test(voice=voice)
        return True

    if text_lower.startswith("/live-reply-gate-test ") or text_lower.startswith("/phase19-5-test "):
        print_live_reply_gate_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase19-ready", "/phase19-5-ready", "/p19-ready", "/p19-5-ready"}:
        print_phase19_ready(voice)
        return True

    if text_lower in {"/voice-binding-status", "/phase20-1-status", "/p20-1"}:
        print_voice_binding_status(voice) if text_lower == "/voice-binding-status" else print_phase20_1_status(voice)
        return True

    if text_lower in {"/voice-binding-guard-status", "/phase20-1-guard-status"}:
        print_voice_binding_guard_status(voice)
        return True

    if text_lower in {"/voice-binding-test", "/phase20-1-test"}:
        print_voice_binding_test(voice=voice)
        return True

    if text_lower.startswith("/voice-binding-test ") or text_lower.startswith("/phase20-1-test "):
        print_voice_binding_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase20-1-ready", "/p20-1-ready"}:
        print_phase20_1_ready(voice)
        return True

    if text_lower in {"/voice-dispatch-status", "/phase20-2-status", "/p20-2"}:
        print_voice_dispatch_status(voice) if text_lower == "/voice-dispatch-status" else print_phase20_2_status(voice)
        return True

    if text_lower in {"/voice-dispatch-guard-status", "/phase20-2-guard-status"}:
        print_voice_dispatch_guard_status(voice)
        return True

    if text_lower in {"/voice-dispatch-test", "/phase20-2-test"}:
        print_voice_dispatch_test(voice=voice)
        return True

    if text_lower.startswith("/voice-dispatch-test ") or text_lower.startswith("/phase20-2-test "):
        print_voice_dispatch_test(text.split(" ", 1)[1], voice=voice)
        return True

    if text_lower in {"/phase20-2-ready", "/p20-2-ready"}:
        print_phase20_2_ready(voice)
        return True

    if text_lower in {"/expression-dispatch-status", "/phase20-3-status", "/p20-3"}:
        print_expression_dispatch_status(vts, voice) if text_lower == "/expression-dispatch-status" else print_phase20_3_status(vts, voice)
        return True

    if text_lower in {"/expression-dispatch-guard-status", "/phase20-3-guard-status"}:
        print_expression_dispatch_guard_status(vts, voice)
        return True

    if text_lower in {"/expression-dispatch-test", "/phase20-3-test"}:
        print_expression_dispatch_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/expression-dispatch-test ") or text_lower.startswith("/phase20-3-test "):
        print_expression_dispatch_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase20-3-ready", "/p20-3-ready"}:
        print_phase20_3_ready(vts, voice)
        return True

    if text_lower in {"/live-speech-safety-status", "/phase20-4-status", "/p20-4"}:
        print_live_speech_safety_status(vts, voice) if text_lower == "/live-speech-safety-status" else print_phase20_4_status(vts, voice)
        return True

    if text_lower in {"/live-speech-safety-guard-status", "/phase20-4-guard-status"}:
        print_live_speech_safety_guard_status(vts, voice)
        return True

    if text_lower in {"/live-speech-safety-test", "/phase20-4-test"}:
        print_live_speech_safety_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/live-speech-safety-test ") or text_lower.startswith("/phase20-4-test "):
        print_live_speech_safety_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase20-4-ready", "/p20-4-ready"}:
        print_phase20_4_ready(vts, voice)
        return True

    if text_lower in {"/voice-gate-status", "/phase20-status", "/phase20-5-status", "/p20", "/p20-5"}:
        print_voice_gate_status(vts, voice) if text_lower == "/voice-gate-status" else print_phase20_5_status(vts, voice)
        return True

    if text_lower in {"/voice-gate-guard-status", "/phase20-5-guard-status"}:
        print_voice_gate_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-gate-test", "/phase20-5-test"}:
        print_voice_gate_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-gate-test ") or text_lower.startswith("/phase20-5-test "):
        print_voice_gate_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase20-ready", "/phase20-5-ready", "/p20-ready", "/p20-5-ready"}:
        print_phase20_ready(vts, voice)
        return True

    if text_lower in {"/live-voice-control-status", "/phase21-1-status", "/p21-1"}:
        print_live_voice_control_status(vts, voice) if text_lower == "/live-voice-control-status" else print_phase21_1_status(vts, voice)
        return True

    if text_lower in {"/live-voice-control-guard-status", "/phase21-1-guard-status"}:
        print_live_voice_control_guard_status(vts, voice)
        return True

    if text_lower in {"/live-voice-control-test", "/phase21-1-test"}:
        print_live_voice_control_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/live-voice-control-test ") or text_lower.startswith("/phase21-1-test "):
        print_live_voice_control_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase21-1-ready", "/p21-1-ready"}:
        print_phase21_1_ready(vts, voice)
        return True

    if text_lower in {"/direct-voice-pilot-status", "/phase21-2-status", "/p21-2"}:
        print_direct_voice_pilot_status(vts, voice) if text_lower == "/direct-voice-pilot-status" else print_phase21_2_status(vts, voice)
        return True

    if text_lower in {"/direct-voice-pilot-guard-status", "/phase21-2-guard-status"}:
        print_direct_voice_pilot_guard_status(vts, voice)
        return True

    if text_lower in {"/direct-voice-pilot-test", "/phase21-2-test"}:
        print_direct_voice_pilot_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/direct-voice-pilot-test ") or text_lower.startswith("/phase21-2-test "):
        print_direct_voice_pilot_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase21-2-ready", "/p21-2-ready"}:
        print_phase21_2_ready(vts, voice)
        return True

    if text_lower in {"/guarded-voice-dispatch-status", "/phase21-3-status", "/p21-3"}:
        print_guarded_voice_dispatch_status(vts, voice) if text_lower == "/guarded-voice-dispatch-status" else print_phase21_3_status(vts, voice)
        return True

    if text_lower in {"/guarded-voice-dispatch-guard-status", "/phase21-3-guard-status"}:
        print_guarded_voice_dispatch_guard_status(vts, voice)
        return True

    if text_lower in {"/guarded-voice-dispatch-test", "/phase21-3-test"}:
        print_guarded_voice_dispatch_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/guarded-voice-dispatch-test ") or text_lower.startswith("/phase21-3-test "):
        print_guarded_voice_dispatch_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase21-3-ready", "/p21-3-ready"}:
        print_phase21_3_ready(vts, voice)
        return True

    if text_lower in {"/live-path-replacement-status", "/phase21-4-status", "/p21-4"}:
        print_live_path_replacement_status(vts, voice) if text_lower == "/live-path-replacement-status" else print_phase21_4_status(vts, voice)
        return True

    if text_lower in {"/live-path-replacement-guard-status", "/phase21-4-guard-status"}:
        print_live_path_replacement_guard_status(vts, voice)
        return True

    if text_lower in {"/live-path-replacement-test", "/phase21-4-test"}:
        print_live_path_replacement_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/live-path-replacement-test ") or text_lower.startswith("/phase21-4-test "):
        print_live_path_replacement_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase21-4-ready", "/p21-4-ready"}:
        print_phase21_4_ready(vts, voice)
        return True

    if text_lower in {"/live-voice-gate-status", "/phase21-status", "/phase21-5-status", "/p21", "/p21-5"}:
        print_live_voice_gate_status(vts, voice) if text_lower == "/live-voice-gate-status" else print_phase21_5_status(vts, voice)
        return True

    if text_lower in {"/live-voice-gate-guard-status", "/phase21-5-guard-status"}:
        print_live_voice_gate_guard_status(vts, voice)
        return True

    if text_lower in {"/live-voice-gate-test", "/phase21-5-test"}:
        print_live_voice_gate_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/live-voice-gate-test ") or text_lower.startswith("/phase21-5-test "):
        print_live_voice_gate_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase21-ready", "/phase21-5-ready", "/p21-ready", "/p21-5-ready"}:
        print_phase21_ready(vts, voice)
        return True

    if text_lower in {"/voice-latency-baseline-status", "/phase22-1-status", "/p22-1"}:
        print_voice_latency_baseline_status(vts, voice) if text_lower == "/voice-latency-baseline-status" else print_phase22_1_status(vts, voice)
        return True

    if text_lower in {"/voice-latency-baseline-guard-status", "/phase22-1-guard-status"}:
        print_voice_latency_baseline_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-latency-baseline-test", "/phase22-1-test"}:
        print_voice_latency_baseline_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-latency-baseline-test ") or text_lower.startswith("/phase22-1-test "):
        print_voice_latency_baseline_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase22-1-ready", "/p22-1-ready"}:
        print_phase22_1_ready(vts, voice)
        return True

    if text_lower in {"/voice-latency-design-status", "/phase22-2-status", "/p22-2"}:
        print_voice_latency_design_status(vts, voice) if text_lower == "/voice-latency-design-status" else print_phase22_2_status(vts, voice)
        return True

    if text_lower in {"/voice-latency-design-guard-status", "/phase22-2-guard-status"}:
        print_voice_latency_design_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-latency-design-test", "/phase22-2-test"}:
        print_voice_latency_design_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-latency-design-test ") or text_lower.startswith("/phase22-2-test "):
        print_voice_latency_design_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase22-2-ready", "/p22-2-ready"}:
        print_phase22_2_ready(vts, voice)
        return True

    if text_lower in {"/voice-engine-patch-status", "/phase22-3-status", "/p22-3"}:
        print_voice_engine_patch_status(vts, voice) if text_lower == "/voice-engine-patch-status" else print_phase22_3_status(vts, voice)
        return True

    if text_lower in {"/voice-engine-patch-guard-status", "/phase22-3-guard-status"}:
        print_voice_engine_patch_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-engine-patch-test", "/phase22-3-test"}:
        print_voice_engine_patch_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-engine-patch-test ") or text_lower.startswith("/phase22-3-test "):
        print_voice_engine_patch_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase22-3-ready", "/p22-3-ready"}:
        print_phase22_3_ready(vts, voice)
        return True

    if text_lower in {"/voice-engine-impl-status", "/phase22-4-status", "/p22-4"}:
        print_voice_engine_impl_status(vts, voice) if text_lower == "/voice-engine-impl-status" else print_phase22_4_status(vts, voice)
        return True

    if text_lower in {"/voice-engine-impl-guard-status", "/phase22-4-guard-status"}:
        print_voice_engine_impl_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-engine-impl-test", "/phase22-4-test"}:
        print_voice_engine_impl_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-engine-impl-test ") or text_lower.startswith("/phase22-4-test "):
        print_voice_engine_impl_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase22-4-ready", "/p22-4-ready"}:
        print_phase22_4_ready(vts, voice)
        return True

    if text_lower in {"/voice-latency-gate-status", "/phase22-status", "/phase22-5-status", "/p22", "/p22-5"}:
        print_voice_latency_gate_status(vts, voice) if text_lower == "/voice-latency-gate-status" else print_phase22_5_status(vts, voice)
        return True

    if text_lower in {"/voice-latency-gate-guard-status", "/phase22-5-guard-status"}:
        print_voice_latency_gate_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-latency-gate-test", "/phase22-5-test"}:
        print_voice_latency_gate_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-latency-gate-test ") or text_lower.startswith("/phase22-5-test "):
        print_voice_latency_gate_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase22-ready", "/phase22-5-ready", "/p22-ready", "/p22-5-ready"}:
        print_phase22_ready(vts, voice)
        return True

    if text_lower in {"/voice-telemetry-status", "/phase23-1-status", "/p23-1"}:
        print_voice_telemetry_status(vts, voice) if text_lower == "/voice-telemetry-status" else print_phase23_1_status(vts, voice)
        return True

    if text_lower in {"/voice-telemetry-guard-status", "/phase23-1-guard-status"}:
        print_voice_telemetry_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-telemetry-test", "/phase23-1-test"}:
        print_voice_telemetry_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-telemetry-test ") or text_lower.startswith("/phase23-1-test "):
        print_voice_telemetry_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase23-1-ready", "/p23-1-ready"}:
        print_phase23_1_ready(vts, voice)
        return True

    if text_lower in {"/voice-streaming-decision-status", "/phase23-2-status", "/p23-2"}:
        print_voice_streaming_decision_status(vts, voice) if text_lower == "/voice-streaming-decision-status" else print_phase23_2_status(vts, voice)
        return True

    if text_lower in {"/voice-streaming-decision-guard-status", "/phase23-2-guard-status"}:
        print_voice_streaming_decision_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-streaming-decision-test", "/phase23-2-test"}:
        print_voice_streaming_decision_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-streaming-decision-test ") or text_lower.startswith("/phase23-2-test "):
        print_voice_streaming_decision_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase23-2-ready", "/p23-2-ready"}:
        print_phase23_2_ready(vts, voice)
        return True

    if text_lower in {"/voice-streaming-dry-run-status", "/phase23-3-status", "/p23-3"}:
        print_voice_streaming_dry_run_status(vts, voice) if text_lower == "/voice-streaming-dry-run-status" else print_phase23_3_status(vts, voice)
        return True

    if text_lower in {"/voice-streaming-dry-run-guard-status", "/phase23-3-guard-status"}:
        print_voice_streaming_dry_run_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-streaming-dry-run-test", "/phase23-3-test"}:
        print_voice_streaming_dry_run_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-streaming-dry-run-test ") or text_lower.startswith("/phase23-3-test "):
        print_voice_streaming_dry_run_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase23-3-ready", "/p23-3-ready"}:
        print_phase23_3_ready(vts, voice)
        return True

    if text_lower in {"/voice-stream-safety-status", "/phase23-4-status", "/p23-4"}:
        print_voice_stream_safety_status(vts, voice) if text_lower == "/voice-stream-safety-status" else print_phase23_4_status(vts, voice)
        return True

    if text_lower in {"/voice-stream-safety-guard-status", "/phase23-4-guard-status"}:
        print_voice_stream_safety_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-stream-safety-test", "/phase23-4-test"}:
        print_voice_stream_safety_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-stream-safety-test ") or text_lower.startswith("/phase23-4-test "):
        print_voice_stream_safety_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase23-4-ready", "/p23-4-ready"}:
        print_phase23_4_ready(vts, voice)
        return True

    if text_lower in {"/voice-stream-gate-status", "/phase23-status", "/phase23-5-status", "/p23", "/p23-5"}:
        print_voice_stream_gate_status(vts, voice) if text_lower == "/voice-stream-gate-status" else print_phase23_5_status(vts, voice)
        return True

    if text_lower in {"/voice-stream-gate-guard-status", "/phase23-5-guard-status"}:
        print_voice_stream_gate_guard_status(vts, voice)
        return True

    if text_lower in {"/voice-stream-gate-test", "/phase23-5-test"}:
        print_voice_stream_gate_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/voice-stream-gate-test ") or text_lower.startswith("/phase23-5-test "):
        print_voice_stream_gate_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase23-ready", "/phase23-5-ready", "/p23-ready", "/p23-5-ready"}:
        print_phase23_ready(vts, voice)
        return True

    if text_lower in {"/stream-pilot-control-status", "/phase24-1-status", "/p24-1"}:
        print_stream_pilot_control_status(vts, voice) if text_lower == "/stream-pilot-control-status" else print_phase24_1_status(vts, voice)
        return True

    if text_lower in {"/stream-pilot-control-guard-status", "/phase24-1-guard-status"}:
        print_stream_pilot_control_guard_status(vts, voice)
        return True

    if text_lower in {"/stream-pilot-control-test", "/phase24-1-test"}:
        print_stream_pilot_control_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/stream-pilot-control-test ") or text_lower.startswith("/phase24-1-test "):
        print_stream_pilot_control_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase24-1-ready", "/p24-1-ready"}:
        print_phase24_1_ready(vts, voice)
        return True

    if text_lower in {"/guarded-stream-call-status", "/phase24-2-status", "/p24-2"}:
        print_guarded_stream_call_status(vts, voice) if text_lower == "/guarded-stream-call-status" else print_phase24_2_status(vts, voice)
        return True

    if text_lower in {"/guarded-stream-call-guard-status", "/phase24-2-guard-status"}:
        print_guarded_stream_call_guard_status(vts, voice)
        return True

    if text_lower in {"/guarded-stream-call-test", "/phase24-2-test"}:
        print_guarded_stream_call_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/guarded-stream-call-test ") or text_lower.startswith("/phase24-2-test "):
        print_guarded_stream_call_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase24-2-ready", "/p24-2-ready"}:
        print_phase24_2_ready(vts, voice)
        return True

    if text_lower in {"/controlled-stream-pilot-status", "/phase24-3-status", "/p24-3"}:
        print_controlled_stream_pilot_status(vts, voice) if text_lower == "/controlled-stream-pilot-status" else print_phase24_3_status(vts, voice)
        return True

    if text_lower in {"/controlled-stream-pilot-guard-status", "/phase24-3-guard-status"}:
        print_controlled_stream_pilot_guard_status(vts, voice)
        return True

    if text_lower in {"/controlled-stream-pilot-test", "/phase24-3-test"}:
        print_controlled_stream_pilot_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/controlled-stream-pilot-test ") or text_lower.startswith("/phase24-3-test "):
        print_controlled_stream_pilot_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase24-3-ready", "/p24-3-ready"}:
        print_phase24_3_ready(vts, voice)
        return True

    if text_lower in {"/stream-rollback-status", "/phase24-4-status", "/p24-4"}:
        print_stream_rollback_status(vts, voice) if text_lower == "/stream-rollback-status" else print_phase24_4_status(vts, voice)
        return True

    if text_lower in {"/stream-rollback-guard-status", "/phase24-4-guard-status"}:
        print_stream_rollback_guard_status(vts, voice)
        return True

    if text_lower in {"/stream-rollback-test", "/phase24-4-test"}:
        print_stream_rollback_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/stream-rollback-test ") or text_lower.startswith("/phase24-4-test "):
        print_stream_rollback_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase24-4-ready", "/p24-4-ready"}:
        print_phase24_4_ready(vts, voice)
        return True

    if text_lower in {"/controlled-stream-gate-status", "/phase24-status", "/phase24-5-status", "/p24", "/p24-5"}:
        print_controlled_stream_gate_status(vts, voice) if text_lower == "/controlled-stream-gate-status" else print_phase24_5_status(vts, voice)
        return True

    if text_lower in {"/controlled-stream-gate-guard-status", "/phase24-5-guard-status"}:
        print_controlled_stream_gate_guard_status(vts, voice)
        return True

    if text_lower in {"/controlled-stream-gate-test", "/phase24-5-test"}:
        print_controlled_stream_gate_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/controlled-stream-gate-test ") or text_lower.startswith("/phase24-5-test "):
        print_controlled_stream_gate_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase24-ready", "/phase24-5-ready", "/p24-ready", "/p24-5-ready"}:
        print_phase24_ready(vts, voice)
        return True

    if text_lower in {"/live-stream-measurement-status", "/phase25-1-status", "/p25-1"}:
        print_live_stream_measurement_status(vts, voice) if text_lower == "/live-stream-measurement-status" else print_phase25_1_status(vts, voice)
        return True

    if text_lower in {"/live-stream-measurement-guard-status", "/phase25-1-guard-status"}:
        print_live_stream_measurement_guard_status(vts, voice)
        return True

    if text_lower in {"/live-stream-measurement-test", "/phase25-1-test"}:
        print_live_stream_measurement_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/live-stream-measurement-test ") or text_lower.startswith("/phase25-1-test "):
        print_live_stream_measurement_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase25-1-ready", "/p25-1-ready"}:
        print_phase25_1_ready(vts, voice)
        return True

    if text_lower in {"/stream-pilot-enable-status", "/phase25-2-status", "/p25-2"}:
        print_stream_pilot_enable_status(vts, voice) if text_lower == "/stream-pilot-enable-status" else print_phase25_2_status(vts, voice)
        return True

    if text_lower in {"/stream-pilot-enable-guard-status", "/phase25-2-guard-status"}:
        print_stream_pilot_enable_guard_status(vts, voice)
        return True

    if text_lower in {"/stream-pilot-enable-test", "/phase25-2-test"}:
        print_stream_pilot_enable_test(vts=vts, voice=voice)
        return True

    if text_lower.startswith("/stream-pilot-enable-test ") or text_lower.startswith("/phase25-2-test "):
        print_stream_pilot_enable_test(text.split(" ", 1)[1], vts=vts, voice=voice)
        return True

    if text_lower in {"/phase25-2-ready", "/p25-2-ready"}:
        print_phase25_2_ready(vts, voice)
        return True

    for phase in range(26, 33):
        if text_lower in {f"/phase{phase}-status", f"/p{phase}"}:
            print_final_phase_status(phase, vts, voice)
            return True
        if text_lower in {f"/phase{phase}-guard-status"}:
            print_final_phase_guard_status(phase, vts, voice)
            return True
        if text_lower in {f"/phase{phase}-test"}:
            print_final_phase_test(phase, vts=vts, voice=voice)
            return True
        if text_lower.startswith(f"/phase{phase}-test "):
            print_final_phase_test(phase, text.split(" ", 1)[1], vts=vts, voice=voice)
            return True
        if text_lower in {f"/phase{phase}-ready", f"/p{phase}-ready"}:
            print_final_phase_ready(phase, vts, voice)
            return True


    return False
