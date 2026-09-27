#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define NANA_SESSION_AUDIO_CHUNK_BYTES 1024U

typedef esp_err_t (*nana_session_audio_emit_fn)(
    const char *message,
    void *context);

typedef esp_err_t (*nana_session_audio_credit_fn)(
    uint32_t stream_id,
    uint32_t bytes_received,
    void *context);

esp_err_t nana_session_audio_init(
    nana_session_audio_emit_fn emit,
    nana_session_audio_credit_fn emit_credit,
    void *emit_context);

esp_err_t nana_session_audio_deinit(uint32_t timeout_ms);

esp_err_t nana_session_audio_begin(
    uint32_t stream_id,
    uint32_t sample_rate,
    uint32_t channels,
    uint32_t sample_width,
    uint32_t total_bytes,
    uint32_t total_samples,
    uint32_t chunk_bytes,
    bool streaming);

esp_err_t nana_session_audio_enqueue_frame(
    const uint8_t *frame,
    size_t frame_bytes);

esp_err_t nana_session_audio_end(
    uint32_t stream_id,
    uint32_t total_bytes,
    uint32_t chunks);

esp_err_t nana_session_audio_abort(uint32_t stream_id, const char *reason);
void nana_session_audio_connection_closed(void);
