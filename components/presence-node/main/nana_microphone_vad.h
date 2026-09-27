#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

#define NANA_MICROPHONE_SAMPLE_RATE_HZ 16000U
#define NANA_MICROPHONE_MAX_CAPTURE_SECONDS 20U
#define NANA_MICROPHONE_MAX_CAPTURE_BYTES                                  \
    (NANA_MICROPHONE_SAMPLE_RATE_HZ * NANA_MICROPHONE_MAX_CAPTURE_SECONDS * \
     sizeof(int16_t))

typedef bool (*nana_microphone_capture_gate_fn_t)(void);

typedef struct {
    int16_t *samples;
    size_t sample_count;
    size_t trim_samples;
    double noise_dbfs;
    double start_dbfs;
    char stop_reason[24];
} nana_microphone_capture_t;

esp_err_t nana_microphone_run_vad_serial_capture(void);
esp_err_t nana_microphone_run_vad_serial_capture_once(
    uint32_t sequence,
    uint32_t min_utterance_ms,
    uint32_t start_delay_ms,
    const char *profile,
    const char *display_protocol,
    nana_microphone_capture_gate_fn_t capture_gate);

esp_err_t nana_microphone_capture_vad_to_psram(
    uint32_t sequence,
    uint32_t min_utterance_ms,
    uint32_t start_delay_ms,
    const char *profile,
    const char *display_protocol,
    nana_microphone_capture_gate_fn_t capture_gate,
    nana_microphone_capture_t *capture);

void nana_microphone_capture_release(nana_microphone_capture_t *capture);
