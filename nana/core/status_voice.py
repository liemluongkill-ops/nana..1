"""Voice, subtitle, and stage-output status panels for Nana."""

from __future__ import annotations

import os

from nana.core.format import shorten_line


def _has_configured_secret(value: object) -> bool:
    text = str(value or "").strip()
    return bool(text and text not in {"OPENAI_KEY_CUA_BAN", "ELEVENLABS_KEY_CUA_BAN", "YOUR_API_KEY", "YOUR_ELEVEN_API_KEY"})

def _voice_status_snapshot(voice=None) -> dict:
    from nana.config import (
        DEBUG_NO_TTS,
        ELEVEN_API_KEY,
        ELEVEN_OUTPUT_FORMAT,
        VOICE_CACHE_DIR,
        VOICE_CACHE_ENABLED,
        VOICE_CHUNKING_ENABLED,
        VOICE_CHUNK_MAX_CHARS,
        VOICE_ID,
        VOICE_STREAMING_DIRECT_ONLY,
        VOICE_STREAMING_DRY_RUN_ENABLED,
        VOICE_STREAMING_ENABLED,
        VOICE_STREAMING_KILL_SWITCH,
        VOICE_STREAMING_PILOT_ENABLED,
        VOICE_TEST_MODE,
    )

    configured = _has_configured_secret(ELEVEN_API_KEY) and bool(str(VOICE_ID or "").strip())
    disabled = bool(DEBUG_NO_TTS or VOICE_TEST_MODE)
    ready = bool(configured and not disabled)
    mode = "ready_no_call" if ready else ("configured_disabled" if configured else "unconfigured")
    runtime = {}
    runtime_available = False
    if voice is not None:
        snap_fn = getattr(voice, "snapshot", None)
        if callable(snap_fn):
            try:
                runtime = dict(snap_fn())
                runtime_available = True
            except Exception as exc:
                runtime = {"last_error": repr(exc)}

    return {
        "provider": "elevenlabs" if _has_configured_secret(ELEVEN_API_KEY) else "none",
        "configured": configured,
        "ready": ready,
        "mode": mode,
        "disabled": disabled,
        "debug_no_tts": bool(DEBUG_NO_TTS),
        "test_mode": bool(VOICE_TEST_MODE),
        "voice_id_present": bool(str(VOICE_ID or "").strip()),
        "output_format": ELEVEN_OUTPUT_FORMAT,
        "cache_enabled": bool(VOICE_CACHE_ENABLED),
        "cache_dir": str(VOICE_CACHE_DIR),
        "chunking_enabled": bool(VOICE_CHUNKING_ENABLED),
        "chunk_max_chars": VOICE_CHUNK_MAX_CHARS,
        "streaming_enabled": bool(VOICE_STREAMING_ENABLED),
        "streaming_dry_run_enabled": bool(VOICE_STREAMING_DRY_RUN_ENABLED),
        "streaming_pilot_enabled": bool(VOICE_STREAMING_PILOT_ENABLED),
        "streaming_kill_switch": bool(VOICE_STREAMING_KILL_SWITCH),
        "streaming_direct_only": bool(VOICE_STREAMING_DIRECT_ONLY),
        "runtime_available": runtime_available,
        "runtime": runtime,
        "voice_call": False,
    }


def _optional_ms(value):
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_ms(value):
    parsed = _optional_ms(value)
    return "none" if parsed is None else f"{parsed:.0f}ms"


def interaction_latency_snapshot(voice=None) -> dict:
    """Combine the last LLM and ElevenLabs measurements without new calls."""

    from nana.brain.llmgate_client import llmgate_transport_snapshot

    llm = llmgate_transport_snapshot()
    voice_status = _voice_status_snapshot(voice)
    runtime = dict(voice_status.get("runtime") or {})

    presence_status = str(runtime.get("last_presence_pcm_status") or "none")
    ttd_status = str(runtime.get("last_private_voice_ttd_status") or "none")
    ttd_first_chunk = _optional_ms(
        runtime.get("last_private_voice_ttd_first_chunk_ms")
    )
    presence_first_byte = _optional_ms(
        runtime.get("last_presence_pcm_first_byte_ms")
    )
    if presence_first_byte is not None and presence_status != "none":
        voice_source = "presence_pcm"
        tts_first_byte = presence_first_byte
        first_audio = _optional_ms(
            runtime.get("last_presence_pcm_first_audio_ms")
        )
        input_chars = int(runtime.get("last_presence_pcm_input_chars", 0) or 0)
        prepared_chars = int(
            runtime.get("last_presence_pcm_prepared_chars", 0) or 0
        )
        provider_audio_ms = _optional_ms(
            runtime.get("last_presence_pcm_provider_duration_ms")
        )
        voice_runtime_status = presence_status
        timing_scope = "provider"
    elif ttd_first_chunk is not None and ttd_status not in {"none", "bypassed"}:
        voice_source = "ttd_websocket_pcm"
        tts_first_byte = ttd_first_chunk
        first_audio = _optional_ms(
            runtime.get("last_private_voice_ttd_first_audio_ms")
        )
        input_chars = int(
            runtime.get("last_private_voice_ttd_source_chars", 0) or 0
        )
        prepared_chars = int(
            runtime.get("last_private_voice_ttd_prepared_chars", 0) or 0
        )
        provider_audio_ms = _optional_ms(
            runtime.get("last_audio_played_duration_ms")
        )
        voice_runtime_status = ttd_status
        timing_scope = "turn"
    else:
        voice_source = "http_stream"
        tts_first_byte = _optional_ms(
            runtime.get("last_stream_time_to_first_byte_ms")
        )
        first_audio = _optional_ms(
            runtime.get("last_stream_time_to_first_audio_ms")
        )
        input_chars = int(runtime.get("last_tts_text_len", 0) or 0)
        prepared_chars = input_chars
        provider_audio_ms = _optional_ms(
            runtime.get("last_audio_played_duration_ms")
        )
        voice_runtime_status = str(
            runtime.get("last_stream_playback_state") or "none"
        )
        timing_scope = "provider"

    llm_first = _optional_ms(llm.get("first_text_ms"))
    llm_total = _optional_ms(llm.get("total_ms"))
    if timing_scope == "turn":
        earliest_provider_byte = tts_first_byte
        serial_provider_byte = None
        earliest_audible = first_audio
        serial_audible = None
    else:
        earliest_provider_byte = (
            None
            if llm_first is None or tts_first_byte is None
            else llm_first + tts_first_byte
        )
        serial_provider_byte = (
            None
            if llm_total is None or tts_first_byte is None
            else llm_total + tts_first_byte
        )
        earliest_audible = (
            None
            if llm_first is None or first_audio is None
            else llm_first + first_audio
        )
        serial_audible = (
            None
            if llm_total is None or first_audio is None
            else llm_total + first_audio
        )

    return {
        "read_only": True,
        "llm": dict(llm),
        "voice": {
            "source": voice_source,
            "status": voice_runtime_status,
            "input_chars": input_chars,
            "prepared_chars": prepared_chars,
            "tts_first_byte_ms": tts_first_byte,
            "first_audio_ms": first_audio,
            "provider_audio_duration_ms": provider_audio_ms,
            "timing_scope": timing_scope,
        },
        "ttd": {
            "enabled": bool(runtime.get("private_voice_ttd_enabled", False)),
            "status": ttd_status,
            "model": str(runtime.get("private_voice_ttd_model") or "none"),
            "input_mode": str(
                runtime.get("private_voice_ttd_input_mode") or "none"
            ),
            "output_format": str(
                runtime.get("private_voice_ttd_output_format") or "none"
            ),
            "first_text_sent_ms": _optional_ms(
                runtime.get("last_private_voice_ttd_first_text_sent_ms")
            ),
            "first_chunk_ms": ttd_first_chunk,
            "first_audio_ms": _optional_ms(
                runtime.get("last_private_voice_ttd_first_audio_ms")
            ),
            "missing_chars": int(
                runtime.get("last_private_voice_ttd_missing_chars", 0) or 0
            ),
            "duplicate_chars": int(
                runtime.get("last_private_voice_ttd_duplicate_chars", 0) or 0
            ),
            "replayed_chars": int(
                runtime.get("last_private_voice_ttd_replayed_chars", 0) or 0
            ),
        },
        "overlap": {
            "enabled": bool(runtime.get("private_voice_overlap_enabled", False)),
            "status": str(
                runtime.get("last_private_voice_overlap_status") or "none"
            ),
            "turn_first_audio_ms": _optional_ms(
                runtime.get("last_private_voice_overlap_first_audio_ms")
            ),
            "seam_wait_ms": _optional_ms(
                runtime.get("last_private_voice_overlap_seam_wait_ms")
            ),
            "true_overlap": bool(
                runtime.get("last_private_voice_overlap_true_overlap", False)
            ),
            "missing_chars": int(
                runtime.get("last_private_voice_overlap_missing_chars", 0) or 0
            ),
            "duplicate_chars": int(
                runtime.get("last_private_voice_overlap_duplicate_chars", 0) or 0
            ),
        },
        "combined": {
            "earliest_provider_byte_ms": earliest_provider_byte,
            "serial_provider_byte_ms": serial_provider_byte,
            "earliest_audible_ms": earliest_audible,
            "serial_audible_ms": serial_audible,
        },
    }


def print_interaction_latency_status(voice=None) -> None:
    """Read-only last-turn LLM + ElevenLabs latency calculator."""

    snap = interaction_latency_snapshot(voice)
    llm = snap["llm"]
    voice_data = snap["voice"]
    ttd = snap["ttd"]
    overlap = snap["overlap"]
    combined = snap["combined"]
    print("Interaction Latency Status")
    print("  Action: read-only; no LLM, ElevenLabs, TTS, or playback call.")
    print(
        "  LLM: "
        f"model={llm.get('model')} | stream={llm.get('stream')} | "
        f"prompt={llm.get('prompt_chars', 0)}ch | max_tokens={llm.get('max_tokens')} | "
        f"first_text={_format_ms(llm.get('first_text_ms'))} | "
        f"tail_after_first={_format_ms(llm.get('tail_after_first_ms'))} | "
        f"total={_format_ms(llm.get('total_ms'))}"
    )
    print(
        "  ElevenLabs: "
        f"source={voice_data.get('source')} | status={voice_data.get('status')} | "
        f"chars={voice_data.get('prepared_chars')}/{voice_data.get('input_chars')} | "
        f"first_byte={_format_ms(voice_data.get('tts_first_byte_ms'))} | "
        f"first_audio={_format_ms(voice_data.get('first_audio_ms'))} | "
        f"audio_duration={_format_ms(voice_data.get('provider_audio_duration_ms'))}"
    )
    print(
        "  TTD turn: "
        f"enabled={ttd.get('enabled')} | status={ttd.get('status')} | "
        f"model={ttd.get('model')} | mode={ttd.get('input_mode')} | "
        f"format={ttd.get('output_format')} | "
        f"text_sent={_format_ms(ttd.get('first_text_sent_ms'))} | "
        f"first_chunk={_format_ms(ttd.get('first_chunk_ms'))} | "
        f"first_audio={_format_ms(ttd.get('first_audio_ms'))} | "
        f"missing={ttd.get('missing_chars')} | duplicate={ttd.get('duplicate_chars')} | "
        f"replayed={ttd.get('replayed_chars')}"
    )
    print(
        "  Combined provider-byte window: "
        f"earliest={_format_ms(combined.get('earliest_provider_byte_ms'))} | "
        f"serial={_format_ms(combined.get('serial_provider_byte_ms'))}"
    )
    print(
        "  Combined audible window: "
        f"earliest={_format_ms(combined.get('earliest_audible_ms'))} | "
        f"serial={_format_ms(combined.get('serial_audible_ms'))}"
    )
    print(
        "  Actual overlap turn: "
        f"enabled={overlap.get('enabled')} | status={overlap.get('status')} | "
        f"first_audio={_format_ms(overlap.get('turn_first_audio_ms'))} | "
        f"seam={_format_ms(overlap.get('seam_wait_ms'))} | "
        f"true_overlap={overlap.get('true_overlap')} | "
        f"missing={overlap.get('missing_chars')} | "
        f"duplicate={overlap.get('duplicate_chars')}"
    )
    if voice_data.get("timing_scope") == "turn":
        print(
            "  Formula: TTD timestamps are measured from the private voice turn "
            "start; serial HTTP bounds are not applicable."
        )
    else:
        print(
            "  Formula: earliest=LLM first_text + Eleven first_byte | "
            "serial=LLM total + Eleven first_byte"
        )
    if str(voice_data.get("status") or "").lower() not in {
        "done",
        "complete",
        "completed",
        "complete_streaming",
    }:
        print(
            "  Warning: voice is not complete; chars/audio duration may still be "
            "zero or partial. Run this command again after playback stops."
        )
    print(
        "  Interpretation: short one-sentence replies are usually near serial; "
        "long replies may start between the two bounds because LLM and TTS overlap."
    )
    print(
        "  Note: run immediately after audio completes; this correlates the latest "
        "LLM and voice snapshots from the current process."
    )

def print_voice_status(voice=None) -> None:
    """Read-only voice/TTS status. Never calls ElevenLabs or TTS playback."""

    status = _voice_status_snapshot(voice)
    runtime = dict(status.get("runtime") or {})
    queue_size = runtime.get("queue_size")
    queue_max = runtime.get("queue_maxsize")
    queue_text = "unavailable" if queue_size is None or queue_max is None else f"{queue_size}/{queue_max}"
    low_watermark = runtime.get("last_stream_playback_buffer_low_watermark_ms")
    low_watermark_text = "none" if low_watermark is None else f"{low_watermark}ms"
    underflow_count = int(runtime.get("last_stream_output_underflow_count", 0) or 0)
    if underflow_count:
        underflow_snapshot = (
            f"buffer:{runtime.get('last_stream_last_underflow_buffer_ms')}ms,"
            f"queue:{runtime.get('last_stream_last_underflow_queue_depth')},"
            f"decoder_done:{runtime.get('last_stream_last_underflow_decoder_done')}"
        )
    else:
        underflow_snapshot = "none"
    inline_tag_values = runtime.get("last_inline_tags") or ()
    if isinstance(inline_tag_values, str):
        inline_tag_values = (inline_tag_values,)
    inline_tag_text = ",".join(str(value) for value in inline_tag_values) or "none"

    print("🎙️ Voice Status")
    print("  Action: read-only; no ElevenLabs/TTS/playback call.")
    print(
        "  Config: "
        f"provider={status['provider']} | configured={status['configured']} | "
        f"ready={status['ready']} | mode={status['mode']}"
    )
    print(
        "  Flags: "
        f"debug_no_tts={status['debug_no_tts']} | test_mode={status['test_mode']} | "
        f"voice_id_present={status['voice_id_present']} | format={status['output_format']}"
    )
    print(
        "  Eleven HTTP: "
        f"keepalive={runtime.get('voice_http_keepalive_enabled', False)} | "
        f"pool={runtime.get('voice_http_pool_maxsize', 0)} | "
        f"thread_request={runtime.get('last_voice_http_session_request_index', 0)} | "
        f"reused_hint={runtime.get('last_voice_http_session_reused_hint', False)}"
    )
    print(
        "  Runtime: "
        f"available={status['runtime_available']} | worker_alive={runtime.get('worker_alive')} | "
        f"status={runtime.get('status')} | speaking={runtime.get('speaking')} | "
        f"listening={runtime.get('listening')} | queue={queue_text}"
    )
    configured_mic = runtime.get("microphone_configured_index")
    active_mic = runtime.get("microphone_active_index")
    print(
        "  Input: "
        f"mode={runtime.get('microphone_mode', 'unknown')} | "
        f"configured_index={'auto' if configured_mic is None else configured_mic} | "
        f"active_index={'none' if active_mic is None else active_mic} | "
        f"name={runtime.get('microphone_active_name') or 'none'} | "
        f"open={runtime.get('microphone_open_status', 'not_tested')}"
    )
    print(
        "  Queue totals: "
        f"queued={runtime.get('queued_total', 0)} | dropped={runtime.get('dropped_total', 0)}"
    )
    print(
        "  Cache: "
        f"enabled={status['cache_enabled']} | hits={runtime.get('cache_hits', 0)} | "
        f"misses={runtime.get('cache_misses', 0)} | dir={status['cache_dir']}"
    )
    print(
        "  Chunking: "
        f"enabled={status['chunking_enabled']} | max_chars={status['chunk_max_chars']} | "
        f"full_request={runtime.get('voice_full_single_request_enabled')} | "
        f"provider_max={runtime.get('elevenlabs_single_request_max_chars')} | "
        f"fallback_max={runtime.get('voice_story_chunk_max_chars')} | "
        f"last_chunks={runtime.get('last_tts_chunks', 0)} | "
        f"last_policy={runtime.get('last_tts_voice_mode', 'chat')} | "
        f"last_max={runtime.get('last_tts_chunk_max_chars', status['chunk_max_chars'])} | "
        f"strategy={runtime.get('last_tts_strategy', 'none')}"
    )
    print(
        "  Inline tags: "
        f"enabled={runtime.get('voice_inline_audio_tags_enabled', True)} | "
        f"last={runtime.get('last_inline_tag_count', 0)}/"
        f"{runtime.get('voice_inline_audio_tag_max', 5)} | "
        f"tags={inline_tag_text} | "
        f"aliases={runtime.get('last_inline_tag_aliases', 0)} | "
        f"blocked={runtime.get('last_inline_tag_blocked', 0)} | "
        f"overflow={runtime.get('last_inline_tag_overflow', 0)} | "
        f"unknown={runtime.get('last_inline_tag_unknown', 0)} | "
        f"chars={runtime.get('last_inline_tag_tts_chars', 0)}/"
        f"{runtime.get('last_inline_tag_input_chars', 0)}"
    )
    print(
        "  Audio completion: "
        f"state={runtime.get('last_audio_state', 'none')} | "
        f"completed={runtime.get('last_audio_completed', False)} | "
        f"played={runtime.get('last_audio_played_segments', 0)}/"
        f"{runtime.get('last_audio_requested_segments', 0)} | "
        f"fetched={runtime.get('last_audio_fetched_segments', 0)}/"
        f"{runtime.get('last_audio_requested_segments', 0)} | "
        f"remaining_chars={runtime.get('last_audio_remaining_chars', 0)} | "
        f"duration={runtime.get('last_audio_played_duration_ms', 0.0)}ms | "
        f"abort={runtime.get('last_audio_abort_reason', 'none')} | "
        f"watchdog={runtime.get('last_audio_playback_watchdog', 'none')} | "
        f"timeout={runtime.get('last_audio_playback_timeout_ms', 0.0)}ms"
    )
    segment_bytes = runtime.get("last_presence_pcm_segment_bytes") or ()
    if isinstance(segment_bytes, (str, bytes)):
        segment_bytes = (segment_bytes,)
    print(
        "  Presence PCM: "
        f"status={runtime.get('last_presence_pcm_status', 'none')} | "
        f"chars={runtime.get('last_presence_pcm_prepared_chars', 0)}/"
        f"{runtime.get('last_presence_pcm_text_chars', 0)} prepared/source | "
        f"input={runtime.get('last_presence_pcm_input_chars', 0)} | "
        f"limit_enabled={runtime.get('presence_voice_limit_enabled', False)} | "
        f"limit={runtime.get('presence_voice_max_chars', 'none')} | "
        f"truncated={runtime.get('last_presence_pcm_text_truncated', False)} | "
        f"provider={runtime.get('last_presence_pcm_provider_bytes', 0)}B/"
        f"{runtime.get('last_presence_pcm_provider_duration_ms', 0.0)}ms | "
        f"played={runtime.get('last_presence_pcm_bytes', 0)}B | "
        f"ms_per_char={runtime.get('last_presence_pcm_ms_per_prepared_char')} | "
        f"segments={','.join(str(value) for value in segment_bytes) or 'none'} | "
        f"streaming={runtime.get('last_presence_pcm_streaming', False)} | "
        f"underruns={runtime.get('last_presence_pcm_underruns')} | "
        f"playback_ratio={runtime.get('last_presence_pcm_playback_ratio')} | "
        f"grade={runtime.get('last_presence_pcm_playback_grade', 'none')}"
    )
    print(
        "  Presence segments: "
        f"max_chars={runtime.get('presence_tts_segment_max_chars', 'none')} | "
        f"omitted_chars={runtime.get('last_presence_pcm_omitted_chars', 0)}"
    )
    print(
        "  Presence source: "
        f"first_byte={runtime.get('last_presence_pcm_first_byte_ms')}ms | "
        f"first_audio={runtime.get('last_presence_pcm_first_audio_ms')}ms | "
        f"read_chunk={runtime.get('presence_pcm_stream_network_chunk_bytes', 0)}B | "
        f"network={runtime.get('last_presence_pcm_network_chunks', 0)} chunks/"
        f"{runtime.get('last_presence_pcm_network_bytes', 0)}B | "
        f"provider_wait={runtime.get('last_presence_pcm_max_network_gap_ms', 0.0)}ms"
    )
    print(
        "  Presence prefetch: "
        f"enabled={runtime.get('presence_pcm_stream_prefetch_enabled', False)} | "
        f"mode={runtime.get('last_presence_pcm_source_mode', 'pull_safe')} | "
        f"queue={runtime.get('last_presence_pcm_prefetch_high_water', 0)}/"
        f"{runtime.get('presence_pcm_stream_prefetch_chunks', 0)} chunks | "
        f"starvations={runtime.get('last_presence_pcm_prefetch_starvations', 0)} | "
        f"max_wait={runtime.get('last_presence_pcm_max_prefetch_wait_ms', 0.0)}ms"
    )
    print(
        "  Playback timing: "
        f"prebuffer={runtime.get('last_tts_prebuffer_chunks', 0)} chunks/"
        f"{runtime.get('voice_prebuffer_chunks')} max_wait={runtime.get('voice_prebuffer_max_ms')}ms | "
        f"full_batch={runtime.get('voice_full_batch_enabled')} extra={runtime.get('voice_full_batch_extra_ms')}ms | "
        f"last_prebuffer={runtime.get('last_tts_prebuffer_ms')}ms | "
        f"seam_wait={runtime.get('last_tts_seam_wait_ms')}ms | "
        f"avg_seam_wait={runtime.get('last_tts_avg_seam_wait_ms')}ms | "
        f"max_seam_wait={runtime.get('last_tts_max_seam_wait_ms')}ms | "
        f"seam_grade={runtime.get('last_tts_seam_grade', 'none')} | "
        f"policy={runtime.get('last_tts_playback_policy', 'none')} | "
        f"cause={runtime.get('last_tts_seam_cause', 'none')}"
    )
    try:
        from nana.runtime.voice_reply_budget import get_voice_reply_budget

        budget = get_voice_reply_budget().snapshot()
        budget_stats = dict(budget.get("stats") or {})
        budget_last = dict(budget.get("last_result") or {})
        print(
            "  Voice budget: "
            f"chat_max={budget.get('chat_max_chars')} | story_max={budget.get('story_max_chars')} | "
            f"policy={budget_last.get('mode', 'none')} | "
            f"last={budget_last.get('voice_chars', 0)}/{budget_last.get('original_chars', 0)} | "
            f"truncated={budget_stats.get('truncated', 0)}"
        )
    except Exception as exc:
        print(f"  Voice budget: error={type(exc).__name__}: {exc}")
    try:
        from nana.runtime.voice_delivery import get_voice_delivery

        delivery = get_voice_delivery().snapshot()
        delivery_stats = dict(delivery.get("stats") or {})
        delivery_last = dict(delivery.get("last_plan") or {})
        print(
            "  Voice delivery: "
            f"strategy={delivery_last.get('strategy', 'none')} | "
            f"chunks={len(delivery_last.get('chunks') or [])} | "
            f"tail={delivery_last.get('tail_count', 0)} | "
            f"plans={delivery_stats.get('built', 0)}"
        )
    except Exception as exc:
        print(f"  Voice delivery: error={type(exc).__name__}: {exc}")
    print(
        "  Private TTD: "
        f"enabled={runtime.get('private_voice_ttd_enabled', False)} | "
        f"model={runtime.get('private_voice_ttd_model', 'none')} | "
        f"mode={runtime.get('private_voice_ttd_input_mode', 'incremental')} | "
        f"format={runtime.get('private_voice_ttd_output_format', 'none')} | "
        f"threshold={runtime.get('private_voice_ttd_min_chars', 40)}ch/"
        f"{runtime.get('private_voice_ttd_min_words', 8)}w | "
        f"chunk_target={runtime.get('private_voice_ttd_chunk_target_chars', 120)}ch | "
        f"chunk_max={runtime.get('private_voice_ttd_chunk_max_chars', 240)}ch | "
        f"buffer={runtime.get('private_voice_ttd_start_buffer_ms', 300)}ms | "
        f"status={runtime.get('last_private_voice_ttd_status', 'none')} | "
        f"reason={runtime.get('last_private_voice_ttd_reason', 'none')}"
    )
    print(
        "  TTD telemetry: "
        f"chunks={runtime.get('last_private_voice_ttd_sent_chunks', 0)} text/"
        f"{runtime.get('last_private_voice_ttd_audio_chunks', 0)} audio | "
        f"chars={runtime.get('last_private_voice_ttd_prepared_chars', 0)}/"
        f"{runtime.get('last_private_voice_ttd_source_chars', 0)} prepared/source | "
        f"bytes={runtime.get('last_private_voice_ttd_received_bytes', 0)} | "
        f"commit={runtime.get('last_private_voice_ttd_committed_ms')}ms | "
        f"connected={runtime.get('last_private_voice_ttd_connected_ms')}ms | "
        f"text_sent={runtime.get('last_private_voice_ttd_first_text_sent_ms')}ms | "
        f"llm_done={runtime.get('last_private_voice_ttd_llm_completed_ms')}ms | "
        f"first_chunk={runtime.get('last_private_voice_ttd_first_chunk_ms')}ms | "
        f"first_pcm={runtime.get('last_private_voice_ttd_first_pcm_ms')}ms | "
        f"first_audio={runtime.get('last_private_voice_ttd_first_audio_ms')}ms | "
        f"final={runtime.get('last_private_voice_ttd_final_ms')}ms"
    )
    print(
        "  TTD integrity: "
        f"missing={runtime.get('last_private_voice_ttd_missing_chars', 0)} | "
        f"duplicate={runtime.get('last_private_voice_ttd_duplicate_chars', 0)} | "
        f"replayed={runtime.get('last_private_voice_ttd_replayed_chars', 0)} | "
        f"speed_omitted={runtime.get('last_private_voice_ttd_speed_omitted', True)} | "
        f"error={runtime.get('last_private_voice_ttd_error') or 'none'}"
    )
    print(
        "  TTD provider text: "
        f"integrity={runtime.get('last_private_voice_ttd_provider_integrity_ok', False)} | "
        f"chars={runtime.get('last_private_voice_ttd_provider_sent_chars', 0)}/"
        f"{runtime.get('last_private_voice_ttd_provider_expected_chars', 0)} sent/expected | "
        f"missing={runtime.get('last_private_voice_ttd_provider_missing_chars', 0)} | "
        f"duplicate={runtime.get('last_private_voice_ttd_provider_duplicate_chars', 0)} | "
        f"mismatch_index={runtime.get('last_private_voice_ttd_provider_mismatch_index')}"
    )
    print(
        "  TTD capture: "
        f"enabled={runtime.get('private_voice_ttd_capture_enabled', False)} | "
        f"bytes={runtime.get('last_private_voice_ttd_capture_bytes', 0)} | "
        f"silence_spans={runtime.get('last_private_voice_ttd_silence_spans', 0)} | "
        f"silence_total={runtime.get('last_private_voice_ttd_silence_total_ms', 0.0)}ms | "
        f"longest={runtime.get('last_private_voice_ttd_longest_silence_ms', 0.0)}ms | "
        f"wav={runtime.get('last_private_voice_ttd_capture_path') or 'none'} | "
        f"report={runtime.get('last_private_voice_ttd_capture_report_path') or 'none'}"
    )
    print(
        "  Private overlap: "
        f"enabled={runtime.get('private_voice_overlap_enabled', False)} | "
        f"lead={runtime.get('private_voice_overlap_min_chars', 45)}-"
        f"{runtime.get('private_voice_overlap_max_chars', 140)}ch | "
        f"coalesce={runtime.get('private_voice_overlap_coalesce_ms', 150)}ms | "
        f"status={runtime.get('last_private_voice_overlap_status', 'none')} | "
        f"reason={runtime.get('last_private_voice_overlap_reason', 'none')} | "
        f"chars={runtime.get('last_private_voice_overlap_lead_chars', 0)}+"
        f"{runtime.get('last_private_voice_overlap_tail_chars', 0)}/"
        f"{runtime.get('last_private_voice_overlap_full_chars', 0)} | "
        f"exact_missing={runtime.get('last_private_voice_overlap_missing_chars', 0)} | "
        f"duplicate={runtime.get('last_private_voice_overlap_duplicate_chars', 0)}"
    )
    print(
        "  Overlap HTTP PCM: "
        f"enabled={runtime.get('private_voice_overlap_pcm_enabled', False)} | "
        f"format={runtime.get('private_voice_overlap_pcm_output_format', 'none')} | "
        f"buffer={runtime.get('private_voice_overlap_pcm_start_buffer_ms', 300)}ms | "
        f"status={runtime.get('last_private_voice_overlap_pcm_status', 'none')} | "
        f"reason={runtime.get('last_private_voice_overlap_pcm_reason', 'none')} | "
        f"chunks={runtime.get('last_private_voice_overlap_pcm_lead_chunks', 0)}+"
        f"{runtime.get('last_private_voice_overlap_pcm_tail_chunks', 0)} | "
        f"bytes={runtime.get('last_private_voice_overlap_pcm_lead_bytes', 0)}+"
        f"{runtime.get('last_private_voice_overlap_pcm_tail_bytes', 0)} | "
        f"tail_prefetched={runtime.get('last_private_voice_overlap_pcm_tail_buffered_before_lead_eof', False)} | "
        f"fallback={runtime.get('last_private_voice_overlap_pcm_fallback_used', False)} | "
        f"error={runtime.get('last_private_voice_overlap_pcm_error') or 'none'}"
    )
    print(
        "  Overlap PCM timing: "
        f"lead_first={runtime.get('last_private_voice_overlap_pcm_lead_first_byte_ms')}ms | "
        f"lead_eof={runtime.get('last_private_voice_overlap_pcm_lead_eof_ms')}ms | "
        f"tail_first={runtime.get('last_private_voice_overlap_pcm_tail_first_byte_ms')}ms | "
        f"tail_eof={runtime.get('last_private_voice_overlap_pcm_tail_eof_ms')}ms"
    )
    print(
        "  Overlap timing: "
        f"commit={runtime.get('last_private_voice_overlap_lead_committed_ms')}ms | "
        f"llm_done={runtime.get('last_private_voice_overlap_llm_completed_ms')}ms | "
        f"lead_fetch={runtime.get('last_private_voice_overlap_lead_fetch_started_ms')}->"
        f"{runtime.get('last_private_voice_overlap_lead_fetch_completed_ms')}ms | "
        f"tail_fetch={runtime.get('last_private_voice_overlap_tail_fetch_started_ms')}->"
        f"{runtime.get('last_private_voice_overlap_tail_fetch_completed_ms')}ms | "
        f"first_audio={runtime.get('last_private_voice_overlap_first_audio_ms')}ms | "
        f"lead_end={runtime.get('last_private_voice_overlap_lead_playback_ended_ms')}ms | "
        f"seam={runtime.get('last_private_voice_overlap_seam_wait_ms')}ms | "
        f"true_overlap={runtime.get('last_private_voice_overlap_true_overlap', False)}"
    )
    print(
        "  Streaming: "
        f"enabled={status['streaming_enabled']} | dry_run={status['streaming_dry_run_enabled']} | "
        f"pilot={status['streaming_pilot_enabled']} | kill_switch={status['streaming_kill_switch']} | "
        f"direct_only={status['streaming_direct_only']} | "
        f"callback_output={runtime.get('voice_stream_callback_output_enabled', False)}"
    )
    print(
        "  Stream lifecycle: "
        f"transport={runtime.get('last_stream_transport_state', 'idle')} | "
        f"playback={runtime.get('last_stream_playback_state', 'idle')} | "
        f"provider_eof={runtime.get('last_stream_provider_eof', False)} | "
        f"decoder_eof={runtime.get('last_stream_decoder_eof', False)} | "
        f"completed={runtime.get('last_audio_completed', False)} | "
        f"abort={runtime.get('last_stream_abort_reason', 'none')}"
    )
    print(
        "  Stream latency: "
        f"first_byte={runtime.get('last_stream_time_to_first_byte_ms')}ms | "
        f"first_pcm={runtime.get('last_stream_time_to_first_pcm_ms')}ms | "
        f"first_audio={runtime.get('last_stream_time_to_first_audio_ms')}ms | "
        f"received={runtime.get('last_stream_received_bytes', 0)} bytes | "
        f"max_network_gap={runtime.get('last_stream_max_network_gap_ms', 0.0)}ms"
    )
    print(
        "  Stream buffer: "
        f"target={runtime.get('voice_http_stream_start_buffer_ms', 300)}ms | "
        f"start={runtime.get('last_stream_start_buffer_ms', 0.0)}ms | "
        f"rebuffer={runtime.get('last_stream_rebuffer_count', 0)} | "
        f"rebuffer_total={runtime.get('last_stream_rebuffer_total_ms', 0.0)}ms | "
        f"stall_watchdog={runtime.get('voice_http_stream_stall_timeout_s', 45)}s | "
        f"ffmpeg_ready={runtime.get('last_stream_ffmpeg_ready', False)}"
    )
    print(
        "  Output feed: "
        f"writes={runtime.get('last_stream_output_write_calls', 0)} | "
        f"refills={runtime.get('last_stream_refill_count', 0)} | "
        f"true_rebuffers={runtime.get('last_stream_rebuffer_count', 0)} | "
        f"underflows={underflow_count} | "
        f"low_watermark={low_watermark_text} | "
        f"max_feed_gap={runtime.get('last_stream_max_feed_gap_ms', 0.0)}ms | "
        f"last_underflow={underflow_snapshot}"
    )
    print(
        "  Callback output: "
        f"calls={runtime.get('last_stream_callback_calls', 0)} | "
        f"status_underflows={runtime.get('last_stream_callback_status_underflows', 0)} | "
        f"ring_starvations={runtime.get('last_stream_ring_starvation_count', 0)} | "
        f"ring_low={runtime.get('last_stream_ring_low_watermark_ms')}ms | "
        f"max_callback_lateness={runtime.get('last_stream_max_callback_lateness_ms', 0.0)}ms | "
        f"feeder_refills={runtime.get('last_stream_feeder_refill_count', 0)} | "
        f"feeder_done={runtime.get('last_stream_feeder_done', False)} | "
        f"callback_finished={runtime.get('last_stream_callback_finished', False)}"
    )
    print(f"  Last error: {runtime.get('last_error') or 'none'}")
    print("  Safety: voice_call=False | vts_call=False | obs_call=False | real_input=False")

def _subtitle_status_snapshot() -> dict:
    try:
        from pathlib import Path
        from nana.runtime.stream_public_output import read_public_subtitle_status

        status = dict(read_public_subtitle_status())
        path = Path(str(status.get("path") or ""))
        parent = path.parent
        try:
            parent_exists = parent.exists()
            parent_writable = parent_exists and parent.is_dir()
        except Exception:
            parent_exists = False
            parent_writable = False
        status.update(
            {
                "ready": bool(parent_exists and parent_writable),
                "parent": str(parent),
                "parent_exists": bool(parent_exists),
                "parent_writable": bool(parent_writable),
                "obs_api_call": False,
            }
        )
        return status
    except Exception as exc:
        return {
            "decision": "public_subtitle_status_unavailable",
            "path": "",
            "exists": False,
            "bytes": None,
            "age_ms": None,
            "fresh": False,
            "stale_threshold_ms": 10000,
            "line": "",
            "ready": False,
            "parent": "",
            "parent_exists": False,
            "parent_writable": False,
            "file_write": False,
            "subtitle_call": False,
            "voice_call": False,
            "vts_call": False,
            "obs_call": False,
            "obs_api_call": False,
            "real_input": False,
            "submit": False,
            "error": repr(exc),
        }

def print_subtitle_status() -> None:
    """Read-only public subtitle/OBS-file status. Never writes or calls OBS."""

    status = _subtitle_status_snapshot()
    age = status.get("age_ms")
    age_text = "None" if age is None else f"{int(age)}ms"
    line = shorten_line(status.get("line"), 120)

    print("📝 Subtitle Status")
    print("  Action: read-only; no file write, no OBS API call.")
    print(
        "  Output: "
        f"ready={status.get('ready')} | exists={status.get('exists')} | "
        f"fresh={status.get('fresh')} | bytes={status.get('bytes')} | age={age_text}"
    )
    print(f"  Path: {status.get('path')}")
    print(
        "  Parent: "
        f"exists={status.get('parent_exists')} | writable={status.get('parent_writable')} | "
        f"path={status.get('parent')}"
    )
    print(f"  Line preview: {line}")
    print(
        "  Safety: "
        f"file_write={status.get('file_write')} | subtitle_call={status.get('subtitle_call')} | "
        f"voice_call={status.get('voice_call')} | vts_call={status.get('vts_call')} | "
        f"obs_call={status.get('obs_call')} | obs_api_call={status.get('obs_api_call')} | "
        f"real_input={status.get('real_input')}"
    )

def _env_enabled(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}

def print_stage_output_status(voice=None) -> None:
    """Read-only stage output readiness. Never calls TTS, VTS, OBS, or writes files."""

    voice_status = _voice_status_snapshot(voice)
    voice_runtime = dict(voice_status.get("runtime") or {})
    subtitle_status = _subtitle_status_snapshot()

    try:
        from nana.integrations.vts import get_vts_runtime, vts_snapshot

        vts = vts_snapshot(get_vts_runtime())
    except Exception as exc:
        vts = {
            "ready": False,
            "connected": False,
            "last_error": repr(exc),
            "auth_status_label": "unknown",
            "auth_status": None,
        }

    try:
        from nana.runtime.expression_router import get_expression_router

        router = get_expression_router().get_status()
    except Exception as exc:
        router = {
            "enabled": False,
            "vts_available": False,
            "vts_connected": False,
            "cooldown_remaining_seconds": 0.0,
            "missing_count_total": 0,
            "last_missing_expression": repr(exc),
            "catalog_size": 0,
        }

    subtitle_write_env = "NANA_OSU_PUBLIC_SUBTITLE_WRITE_ENABLED"
    subtitle_clear_env = "NANA_OSU_PUBLIC_SUBTITLE_CLEAR_ENABLED"
    result_pipeline_env = "NANA_OSU_RESULT_SUBTITLE_PIPELINE_WRITE_ENABLED"
    queue_size = voice_runtime.get("queue_size", "n/a")
    queue_max = voice_runtime.get("queue_maxsize", "n/a")

    print("🎛️ Stage Output Status")
    print("  Action: read-only; no TTS, no VTS trigger, no subtitle write, no OBS API call.")
    print(
        "  Voice output: "
        f"ready={voice_status['ready']} | provider={voice_status['provider']} | "
        f"runtime={voice_status['runtime_available']} | speaking={voice_runtime.get('speaking')} | "
        f"queue={queue_size}/{queue_max} | last_error={voice_runtime.get('last_error') or 'none'}"
    )
    print(
        "  Subtitle output: "
        f"ready={subtitle_status.get('ready')} | exists={subtitle_status.get('exists')} | "
        f"fresh={subtitle_status.get('fresh')} | file_write={subtitle_status.get('file_write')} | "
        f"obs_api_call={subtitle_status.get('obs_api_call')}"
    )
    print(f"  Subtitle path: {shorten_line(subtitle_status.get('path'), 110)}")
    print(
        "  Subtitle gates: "
        f"write_env={_env_enabled(subtitle_write_env)} ({subtitle_write_env}) | "
        f"clear_env={_env_enabled(subtitle_clear_env)} ({subtitle_clear_env}) | "
        f"result_pipeline_env={_env_enabled(result_pipeline_env)} ({result_pipeline_env})"
    )
    print(
        "  VTS output: "
        f"ready={vts.get('ready')} | connected={vts.get('connected')} | "
        f"auth={vts.get('auth_status_label')} ({vts.get('auth_status')}) | "
        f"last_error={vts.get('last_error') or 'none'}"
    )
    print(
        "  Expression output: "
        f"router_enabled={router.get('enabled')} | available={router.get('vts_available')} | "
        f"connected={router.get('vts_connected')} | cooldown="
        f"{float(router.get('cooldown_remaining_seconds') or 0.0):.1f}s | "
        f"missing={router.get('missing_count_total')} | catalog={router.get('catalog_size')}"
    )
    print(
        "  Safety: "
        f"voice_call=False | vts_call=False | file_write={subtitle_status.get('file_write')} | "
        f"obs_call={subtitle_status.get('obs_call')} | obs_api_call={subtitle_status.get('obs_api_call')} | "
        f"real_input={subtitle_status.get('real_input')} | game_input=False"
    )
    print("  Live verify: /stage-output-status | /voice-status | /subtitle-status | /vts-status")
