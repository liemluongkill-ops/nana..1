#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef esp_err_t (*nana_session_camera_emit_text_fn)(
    const char *message,
    void *context);

typedef esp_err_t (*nana_session_camera_emit_binary_fn)(
    const uint8_t *message,
    size_t message_bytes,
    void *context);

esp_err_t nana_session_camera_init(
    nana_session_camera_emit_text_fn emit_text,
    nana_session_camera_emit_binary_fn emit_binary,
    void *emit_context);

esp_err_t nana_session_camera_deinit(uint32_t timeout_ms);

bool nana_session_camera_available(void);
void nana_session_camera_connected(void);
void nana_session_camera_disconnected(void);

esp_err_t nana_session_camera_request(
    uint32_t request_id,
    uint32_t max_bytes);

esp_err_t nana_session_camera_cancel(uint32_t stream_id);

esp_err_t nana_session_camera_ready(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t max_bytes);

esp_err_t nana_session_camera_credit(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t bytes_received);

esp_err_t nana_session_camera_received(
    uint32_t stream_id,
    uint32_t total_bytes,
    uint32_t chunks);
