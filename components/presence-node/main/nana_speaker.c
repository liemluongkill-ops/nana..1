#include "nana_speaker.h"

#include <stdbool.h>
#include <stdlib.h>

#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

#include "nana_board_pins.h"

#define SPEAKER_CHUNK_FRAMES 256U
#define SPEAKER_DMA_DESC_NUM 8U
#define SPEAKER_DMA_FRAME_NUM 512U
#define SPEAKER_FADE_MS 20U
#define SPEAKER_AMP_WAKE_MS 20U
#define SPEAKER_AMP_SETTLE_SILENCE_MS 40U
#define SPEAKER_PRE_SILENCE_MS 100U
#define SPEAKER_POST_SILENCE_MS 150U

typedef struct {
    int16_t left;
    int16_t right;
} speaker_stereo_frame_t;

struct nana_speaker_stream {
    i2s_chan_handle_t tx_channel;
    uint32_t sample_rate_hz;
    size_t total_samples;
    size_t written_samples;
    int32_t gain_q15;
    bool channel_enabled;
};

static const char *TAG = "nana_speaker";
static bool amplifier_shutdown_ready = false;

static esp_err_t drive_data_idle_low(void)
{
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << NANA_AUDIO_DOUT_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    esp_err_t result = gpio_config(&config);
    if (result == ESP_OK) {
        result = gpio_set_level(NANA_AUDIO_DOUT_GPIO, 0);
    }
    return result;
}

static esp_err_t set_amplifier_enabled(bool enabled)
{
    if (!amplifier_shutdown_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    const esp_err_t result =
        gpio_set_level(NANA_AUDIO_AMP_SD_GPIO, enabled ? 1 : 0);
    if (result == ESP_OK) {
        ESP_LOGI(TAG,
                 "MAX98357 %s: SD=GPIO%d %s",
                 enabled ? "enabled" : "muted",
                 NANA_AUDIO_AMP_SD_GPIO,
                 enabled ? "HIGH" : "LOW");
    }
    return result;
}

esp_err_t nana_speaker_init(void)
{
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << NANA_AUDIO_AMP_SD_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };

    esp_err_t result = gpio_set_level(NANA_AUDIO_AMP_SD_GPIO, 0);
    if (result == ESP_OK) {
        result = gpio_config(&config);
    }
    if (result == ESP_OK) {
        result = gpio_set_level(NANA_AUDIO_AMP_SD_GPIO, 0);
    }
    if (result != ESP_OK) {
        ESP_LOGE(TAG,
                 "MAX98357 shutdown GPIO%d init failed: %s",
                 NANA_AUDIO_AMP_SD_GPIO,
                 esp_err_to_name(result));
        return result;
    }

    result = drive_data_idle_low();
    if (result != ESP_OK) {
        ESP_LOGE(TAG,
                 "MAX98357 idle data GPIO%d init failed: %s",
                 NANA_AUDIO_DOUT_GPIO,
                 esp_err_to_name(result));
        return result;
    }

    amplifier_shutdown_ready = true;
    ESP_LOGI(TAG,
             "MAX98357 hard-muted at startup: SD=GPIO%d LOW | DIN=GPIO%d LOW",
             NANA_AUDIO_AMP_SD_GPIO,
             NANA_AUDIO_DOUT_GPIO);
    return ESP_OK;
}

esp_err_t nana_speaker_mute(void)
{
    esp_err_t result = set_amplifier_enabled(false);
    const esp_err_t idle_result = drive_data_idle_low();
    if (result == ESP_OK) {
        result = idle_result;
    }
    return result;
}

static esp_err_t write_stereo_frames(i2s_chan_handle_t tx_channel,
                                     const speaker_stereo_frame_t *frames,
                                     size_t frame_count)
{
    const size_t expected_bytes = frame_count * sizeof(*frames);
    size_t written_bytes = 0;
    const esp_err_t result = i2s_channel_write(
        tx_channel, frames, expected_bytes, &written_bytes, portMAX_DELAY);
    if (result != ESP_OK) {
        return result;
    }
    return written_bytes == expected_bytes ? ESP_OK : ESP_ERR_INVALID_SIZE;
}

static esp_err_t play_silence(nana_speaker_stream_t *stream,
                              uint32_t duration_ms)
{
    speaker_stereo_frame_t frames[SPEAKER_CHUNK_FRAMES] = {0};
    size_t remaining_frames =
        ((size_t)stream->sample_rate_hz * duration_ms) / 1000U;

    while (remaining_frames > 0) {
        const size_t chunk_frames = remaining_frames < SPEAKER_CHUNK_FRAMES
                                        ? remaining_frames
                                        : SPEAKER_CHUNK_FRAMES;
        const esp_err_t result =
            write_stereo_frames(stream->tx_channel, frames, chunk_frames);
        if (result != ESP_OK) {
            return result;
        }
        remaining_frames -= chunk_frames;
    }
    return ESP_OK;
}

static float fade_envelope(const nana_speaker_stream_t *stream,
                           size_t sample_index)
{
    // A zero length is the streaming sentinel: the final sample count is not
    // known when I2S starts, so keep the gain flat and let stream_end close it.
    if (stream->total_samples == 0) {
        return 1.0f;
    }
    const size_t fade_samples =
        ((size_t)stream->sample_rate_hz * SPEAKER_FADE_MS) / 1000U;
    if (fade_samples == 0) {
        return 1.0f;
    }

    float envelope = 1.0f;
    if (sample_index < fade_samples) {
        envelope = (float)sample_index / (float)fade_samples;
    }

    const size_t remaining_samples =
        stream->total_samples - sample_index - 1U;
    if (remaining_samples < fade_samples) {
        const float fade_out =
            (float)remaining_samples / (float)fade_samples;
        if (fade_out < envelope) {
            envelope = fade_out;
        }
    }
    return envelope;
}

static esp_err_t close_stream(nana_speaker_stream_t *stream,
                              bool require_complete)
{
    if (stream == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    esp_err_t result = ESP_OK;
    if (require_complete &&
        (stream->written_samples == 0 ||
         (stream->total_samples != 0 &&
          stream->written_samples != stream->total_samples))) {
        result = ESP_ERR_INVALID_SIZE;
    }
    // On an aborted stream, mute first.  Writing a tail of silence before
    // muting can leave a stale DMA block audible while the UART reader is
    // already recovering from a payload gap.
    if (!require_complete) {
        const esp_err_t mute_result = set_amplifier_enabled(false);
        if (result == ESP_OK && mute_result != ESP_OK) {
            result = mute_result;
        }
        const esp_err_t idle_result = drive_data_idle_low();
        if (result == ESP_OK && idle_result != ESP_OK) {
            result = idle_result;
        }
    } else if (stream->channel_enabled) {
        const esp_err_t silence_result =
            play_silence(stream, SPEAKER_POST_SILENCE_MS);
        if (result == ESP_OK && silence_result != ESP_OK) {
            result = silence_result;
        }
    }

    if (require_complete) {
        const esp_err_t mute_result = set_amplifier_enabled(false);
        if (result == ESP_OK && mute_result != ESP_OK) {
            result = mute_result;
        }
    }
    if (stream->channel_enabled) {
        const esp_err_t disable_result =
            i2s_channel_disable(stream->tx_channel);
        if (result == ESP_OK && disable_result != ESP_OK) {
            result = disable_result;
        }
    }
    if (stream->tx_channel != NULL) {
        const esp_err_t delete_result = i2s_del_channel(stream->tx_channel);
        if (result == ESP_OK && delete_result != ESP_OK) {
            result = delete_result;
        }
    }
    const esp_err_t idle_result = drive_data_idle_low();
    if (result == ESP_OK && idle_result != ESP_OK) {
        result = idle_result;
    }
    free(stream);
    return result;
}

esp_err_t nana_speaker_stream_begin(uint32_t sample_rate_hz,
                                    size_t total_samples,
                                    int32_t gain_q15,
                                    nana_speaker_stream_t **stream)
{
    if (stream == NULL || sample_rate_hz < 8000U ||
        sample_rate_hz > 48000U || gain_q15 < 0 || gain_q15 > 32768) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!amplifier_shutdown_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    *stream = NULL;
    nana_speaker_stream_t *owner = calloc(1, sizeof(*owner));
    if (owner == NULL) {
        return ESP_ERR_NO_MEM;
    }
    owner->sample_rate_hz = sample_rate_hz;
    owner->total_samples = total_samples;
    owner->gain_q15 = gain_q15;

    i2s_chan_config_t channel_config =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    // Progressive PCM needs enough hardware headroom while the session task
    // handles Wi-Fi control frames.  Each descriptor is 2 KiB here; the full
    // ring is about 256 ms at 16 kHz stereo output.
    channel_config.dma_desc_num = SPEAKER_DMA_DESC_NUM;
    channel_config.dma_frame_num = SPEAKER_DMA_FRAME_NUM;

    esp_err_t result =
        i2s_new_channel(&channel_config, &owner->tx_channel, NULL);
    if (result != ESP_OK) {
        free(owner);
        return result;
    }

    const i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(sample_rate_hz),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = NANA_AUDIO_BCLK_GPIO,
            .ws = NANA_AUDIO_WS_GPIO,
            .dout = NANA_AUDIO_DOUT_GPIO,
            .din = I2S_GPIO_UNUSED,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };

    result = i2s_channel_init_std_mode(owner->tx_channel, &standard_config);
    if (result == ESP_OK) {
        result = i2s_channel_enable(owner->tx_channel);
    }
    if (result != ESP_OK) {
        ESP_LOGE(TAG,
                 "I2S speaker init failed: %s | dma_ring=%uB free=%lu largest=%zu",
                 esp_err_to_name(result),
                 (unsigned)(SPEAKER_DMA_DESC_NUM * SPEAKER_DMA_FRAME_NUM *
                            sizeof(speaker_stereo_frame_t)),
                 (unsigned long)heap_caps_get_free_size(
                     MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
                 heap_caps_get_largest_free_block(
                     MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
        close_stream(owner, false);
        return result;
    }
    owner->channel_enabled = true;

    result = play_silence(owner, SPEAKER_PRE_SILENCE_MS);
    if (result == ESP_OK) {
        result = set_amplifier_enabled(true);
    }
    if (result == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(SPEAKER_AMP_WAKE_MS));
        result = play_silence(owner, SPEAKER_AMP_SETTLE_SILENCE_MS);
    }
    if (result != ESP_OK) {
        close_stream(owner, false);
        return result;
    }

    *stream = owner;
    return ESP_OK;
}

esp_err_t nana_speaker_stream_write(nana_speaker_stream_t *stream,
                                    const int16_t *samples,
                                    size_t sample_count)
{
    if (stream == NULL || samples == NULL || sample_count == 0 ||
        (stream->total_samples != 0 &&
         stream->written_samples + sample_count > stream->total_samples)) {
        return ESP_ERR_INVALID_ARG;
    }

    speaker_stereo_frame_t frames[SPEAKER_CHUNK_FRAMES];
    size_t consumed = 0;
    while (consumed < sample_count) {
        const size_t chunk_samples = sample_count - consumed < SPEAKER_CHUNK_FRAMES
                                         ? sample_count - consumed
                                         : SPEAKER_CHUNK_FRAMES;
        for (size_t i = 0; i < chunk_samples; ++i) {
            const size_t output_index = stream->written_samples + consumed + i;
            const int32_t gained =
                ((int32_t)samples[consumed + i] * stream->gain_q15) >> 15;
            const int16_t output =
                (int16_t)((float)gained * fade_envelope(stream, output_index));
            frames[i].left = output;
            frames[i].right = output;
        }

        const esp_err_t result =
            write_stereo_frames(stream->tx_channel, frames, chunk_samples);
        if (result != ESP_OK) {
            return result;
        }
        consumed += chunk_samples;
    }
    stream->written_samples += sample_count;
    return ESP_OK;
}

esp_err_t nana_speaker_stream_end(nana_speaker_stream_t *stream)
{
    return close_stream(stream, true);
}

void nana_speaker_stream_abort(nana_speaker_stream_t *stream)
{
    if (stream != NULL) {
        close_stream(stream, false);
    }
}

esp_err_t nana_speaker_play_pcm16_mono(const int16_t *samples,
                                       size_t sample_count,
                                       uint32_t sample_rate_hz,
                                       int32_t gain_q15)
{
    nana_speaker_stream_t *stream = NULL;
    esp_err_t result = nana_speaker_stream_begin(
        sample_rate_hz, sample_count, gain_q15, &stream);
    if (result == ESP_OK) {
        result = nana_speaker_stream_write(stream, samples, sample_count);
    }
    if (result == ESP_OK) {
        return nana_speaker_stream_end(stream);
    }
    nana_speaker_stream_abort(stream);
    return result;
}
