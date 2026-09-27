#pragma once

#include <stddef.h>
#include <stdint.h>

#include "esp_err.h"

typedef struct nana_speaker_stream nana_speaker_stream_t;

esp_err_t nana_speaker_init(void);
esp_err_t nana_speaker_mute(void);

esp_err_t nana_speaker_stream_begin(uint32_t sample_rate_hz,
                                    size_t total_samples,
                                    int32_t gain_q15,
                                    nana_speaker_stream_t **stream);
esp_err_t nana_speaker_stream_write(nana_speaker_stream_t *stream,
                                    const int16_t *samples,
                                    size_t sample_count);
esp_err_t nana_speaker_stream_end(nana_speaker_stream_t *stream);
void nana_speaker_stream_abort(nana_speaker_stream_t *stream);

esp_err_t nana_speaker_play_pcm16_mono(const int16_t *samples,
                                       size_t sample_count,
                                       uint32_t sample_rate_hz,
                                       int32_t gain_q15);
