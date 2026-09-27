#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef esp_err_t (*nana_session_uplink_emit_text_fn)(
    const char *message,
    void *context);

typedef esp_err_t (*nana_session_uplink_emit_binary_fn)(
    const uint8_t *message,
    size_t message_bytes,
    void *context);

esp_err_t nana_session_uplink_init(
    nana_session_uplink_emit_text_fn emit_text,
    nana_session_uplink_emit_binary_fn emit_binary,
    void *emit_context);

esp_err_t nana_session_uplink_deinit(uint32_t timeout_ms);

void nana_session_uplink_connected(void);
void nana_session_uplink_disconnected(void);

esp_err_t nana_session_uplink_suspend(uint32_t timeout_ms);
void nana_session_uplink_resume(void);

esp_err_t nana_session_uplink_capture_ready(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t max_bytes);

esp_err_t nana_session_uplink_capture_credit(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t bytes_received);

esp_err_t nana_session_uplink_capture_received(
    uint32_t stream_id,
    uint32_t wire_bytes,
    uint32_t effective_bytes,
    uint32_t chunks);

esp_err_t nana_session_uplink_turn_complete(
    uint32_t stream_id,
    const char *status,
    const char *reason);
