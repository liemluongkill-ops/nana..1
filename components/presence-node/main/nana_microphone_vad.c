#include "nana_microphone_vad.h"

#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "driver/i2s_std.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_sys.h"
#include "esp_rom_uart.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "led_strip_rmt.h"

#include "nana_board_pins.h"

#define VAD_SAMPLE_RATE_HZ NANA_MICROPHONE_SAMPLE_RATE_HZ
#define VAD_CHUNK_FRAMES 256U
#define VAD_DMA_DESC_NUM 4U
#define VAD_DMA_FRAME_NUM 128U
#define VAD_INITIAL_CALIBRATION_MS 2000U
#define VAD_RECALIBRATION_MS 750U
#define VAD_CALIBRATION_MAX_NOISE_DBFS (-25.0)
#define VAD_PRE_ROLL_MS 250U
#define VAD_START_HOLD_MS 160U
#define VAD_END_SILENCE_MS 600U
#define VAD_KEEP_TAIL_MS 250U
#define VAD_MIN_UTTERANCE_MS 3000U
#define VAD_MAX_UTTERANCE_MS (NANA_MICROPHONE_MAX_CAPTURE_SECONDS * 1000U)
#define VAD_START_MARGIN_DB 12.0
#define VAD_START_FLOOR_DBFS (-45.0)
#define VAD_START_CEILING_DBFS (-20.0)
#define VAD_END_HYSTERESIS_DB 7.0
#define VAD_HEALTH_INTERVAL_MS 2000U
#define VAD_GATE_POLL_MS 25U

typedef struct {
    int32_t left;
    int32_t right;
} vad_stereo_frame_t;

typedef struct {
    int64_t sum;
    uint64_t sum_squares;
    int32_t peak;
    size_t count;
} vad_level_t;

typedef struct {
    uint32_t sequence;
    uint32_t crc;
    size_t chunk_count;
    size_t byte_count;
    bool started;
} vad_serial_stream_t;

typedef struct {
    nana_microphone_capture_t *capture;
    size_t capacity_samples;
    bool started;
} vad_psram_stream_t;

typedef struct {
    void *context;
    esp_err_t (*begin)(void *context, double noise_dbfs, double start_dbfs);
    esp_err_t (*write)(
        void *context, const int16_t *samples, size_t sample_count);
    esp_err_t (*end)(
        void *context,
        size_t sample_count,
        size_t trim_samples,
        const char *reason);
    void (*abort)(void *context, const char *reason);
} vad_capture_sink_t;

static const char *TAG = "nana_mic_vad";
static bool vad_has_calibrated_once = false;

static void emit_mic_state(uint32_t sequence, const char *state)
{
    esp_rom_printf(
        "NANA_NODE_STATE seq=%lu component=mic state=%s\r\n",
        (unsigned long)sequence,
        state);
}

static void emit_armed_health(uint32_t sequence,
                              const char *profile,
                              const char *display_protocol)
{
    esp_rom_printf(
        "NANA_NODE_HEALTH seq=%lu "
        "protocol=nana.presence.maintenance.mic.v1 profile=%s "
        "baud=921600 mic=armed speaker=muted turn=idle display=%s\r\n",
        (unsigned long)sequence,
        profile == NULL ? "mic_vad_serial_diag" : profile,
        display_protocol == NULL ? "none" : display_protocol);
}

static bool capture_gate_allows(nana_microphone_capture_gate_fn_t capture_gate)
{
    return capture_gate == NULL || capture_gate();
}

static bool wait_with_capture_gate(uint32_t delay_ms,
                                   nana_microphone_capture_gate_fn_t capture_gate)
{
    uint32_t elapsed_ms = 0U;
    while (elapsed_ms < delay_ms) {
        if (!capture_gate_allows(capture_gate)) {
            return false;
        }
        uint32_t slice_ms = delay_ms - elapsed_ms;
        if (slice_ms > VAD_GATE_POLL_MS) {
            slice_ms = VAD_GATE_POLL_MS;
        }
        vTaskDelay(pdMS_TO_TICKS(slice_ms));
        elapsed_ms += slice_ms;
    }
    return capture_gate_allows(capture_gate);
}

static double level_dbfs(uint64_t sum_squares, size_t count)
{
    if (sum_squares == 0 || count == 0) {
        return -120.0;
    }

    const double rms = sqrt((double)sum_squares / (double)count);
    return 20.0 * log10(rms / 8388607.0);
}

static int32_t slot_to_sample24(int32_t slot)
{
    return slot >> 8;
}

static int16_t sample24_to_pcm16(int32_t sample)
{
    return (int16_t)(sample >> 8);
}

static void level_add(vad_level_t *level, int32_t sample)
{
    const int64_t wide = sample;
    const int32_t magnitude = sample < 0 ? -sample : sample;

    level->sum += wide;
    level->sum_squares += (uint64_t)(wide * wide);
    if (magnitude > level->peak) {
        level->peak = magnitude;
    }
    ++level->count;
}

static esp_err_t init_status_led(led_strip_handle_t *strip)
{
    const led_strip_config_t strip_config = {
        .strip_gpio_num = NANA_STATUS_LED_GPIO,
        .max_leds = 1,
        .led_model = LED_MODEL_WS2812,
        .color_component_format = LED_STRIP_COLOR_COMPONENT_FMT_GRB,
        .flags = {
            .invert_out = false,
        },
    };
    const led_strip_rmt_config_t rmt_config = {
        .clk_src = RMT_CLK_SRC_DEFAULT,
        .resolution_hz = 10U * 1000U * 1000U,
        .mem_block_symbols = 0,
        .flags = {
            .with_dma = false,
        },
    };

    esp_err_t result = led_strip_new_rmt_device(&strip_config, &rmt_config, strip);
    if (result == ESP_OK) {
        result = led_strip_clear(*strip);
    }
    return result;
}

static esp_err_t set_recording_led(led_strip_handle_t strip, bool enabled)
{
    if (!enabled) {
        return led_strip_clear(strip);
    }

    esp_err_t result = led_strip_set_pixel(strip, 0, 0, 12, 24);
    if (result == ESP_OK) {
        result = led_strip_refresh(strip);
    }
    return result;
}

static esp_err_t set_armed_led(led_strip_handle_t strip)
{
    esp_err_t result = led_strip_set_pixel(strip, 0, 0, 12, 0);
    if (result == ESP_OK) {
        result = led_strip_refresh(strip);
    }
    return result;
}

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, size_t size)
{
    for (size_t i = 0; i < size; ++i) {
        crc ^= data[i];
        for (unsigned bit = 0; bit < 8U; ++bit) {
            const uint32_t mask = (uint32_t)-(int32_t)(crc & 1U);
            crc = (crc >> 1U) ^ (0xedb88320U & mask);
        }
    }
    return crc;
}

static void ring_push(int16_t *ring,
                      size_t capacity,
                      size_t *write_index,
                      size_t *sample_count,
                      int16_t sample)
{
    ring[*write_index] = sample;
    *write_index = (*write_index + 1U) % capacity;
    if (*sample_count < capacity) {
        ++*sample_count;
    }
}

static esp_err_t serial_stream_begin(vad_serial_stream_t *stream,
                                     double noise_dbfs,
                                     double start_dbfs)
{
    if (stream == NULL || stream->sequence == 0U || stream->started) {
        return ESP_ERR_INVALID_ARG;
    }

    stream->crc = 0xffffffffU;
    stream->chunk_count = 0U;
    stream->byte_count = 0U;
    stream->started = true;

    esp_log_level_set("*", ESP_LOG_NONE);
    esp_rom_printf(
        "NANA_AUDIO_STREAM_BEGIN seq=%lu rate=%u channels=1 bits=16 "
        "noise_dbfs_x10=%d start_dbfs_x10=%d\r\n",
        (unsigned long)stream->sequence,
        (unsigned)VAD_SAMPLE_RATE_HZ,
        (int)lround(noise_dbfs * 10.0),
        (int)lround(start_dbfs * 10.0));
    return ESP_OK;
}

static esp_err_t serial_stream_write(vad_serial_stream_t *stream,
                                     const int16_t *samples,
                                     size_t sample_count)
{
    if (stream == NULL || !stream->started || samples == NULL ||
        sample_count == 0U) {
        return ESP_ERR_INVALID_ARG;
    }

    const uint8_t *payload = (const uint8_t *)samples;
    const size_t byte_count = sample_count * sizeof(*samples);
    esp_rom_printf(
        "NANA_AUDIO_CHUNK seq=%lu index=%u bytes=%u\r\n",
        (unsigned long)stream->sequence,
        (unsigned)stream->chunk_count,
        (unsigned)byte_count);
    for (size_t i = 0; i < byte_count; ++i) {
        esp_rom_output_tx_one_char((char)payload[i]);
    }
    stream->crc = crc32_update(stream->crc, payload, byte_count);
    ++stream->chunk_count;
    stream->byte_count += byte_count;
    return ESP_OK;
}

static esp_err_t capture_sink_ring(const vad_capture_sink_t *sink,
                                   const int16_t *ring,
                                   size_t capacity,
                                   size_t write_index,
                                   size_t sample_count)
{
    if (sink == NULL || sink->write == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (sample_count == 0U) {
        return ESP_OK;
    }

    const size_t oldest = (write_index + capacity - sample_count) % capacity;
    const size_t first_count =
        sample_count < (capacity - oldest) ? sample_count : (capacity - oldest);
    esp_err_t result = sink->write(sink->context, ring + oldest, first_count);
    if (result == ESP_OK && first_count < sample_count) {
        result = sink->write(
            sink->context, ring, sample_count - first_count);
    }
    return result;
}

static esp_err_t serial_stream_end(vad_serial_stream_t *stream,
                                   size_t sample_count,
                                   size_t trim_samples,
                                   const char *reason)
{
    if (stream == NULL || !stream->started || reason == NULL ||
        trim_samples > sample_count ||
        stream->byte_count != sample_count * sizeof(int16_t)) {
        return ESP_ERR_INVALID_ARG;
    }

    const uint32_t crc = ~stream->crc;
    esp_rom_printf(
        "NANA_AUDIO_STREAM_END seq=%lu chunks=%u bytes=%u samples=%u "
        "crc32=%08lx duration_ms=%u trim_samples=%u reason=%s\r\n",
        (unsigned long)stream->sequence,
        (unsigned)stream->chunk_count,
        (unsigned)stream->byte_count,
        (unsigned)sample_count,
        (unsigned long)crc,
        (unsigned)((sample_count * 1000U) / VAD_SAMPLE_RATE_HZ),
        (unsigned)trim_samples,
        reason);
    vTaskDelay(pdMS_TO_TICKS(100));
    esp_log_level_set("*", ESP_LOG_INFO);
    stream->started = false;
    return ESP_OK;
}

static void serial_stream_abort(vad_serial_stream_t *stream, const char *reason)
{
    if (stream == NULL || !stream->started) {
        return;
    }
    esp_rom_printf(
        "NANA_AUDIO_STREAM_ABORT seq=%lu reason=%s\r\n",
        (unsigned long)stream->sequence,
        reason == NULL ? "capture_error" : reason);
    vTaskDelay(pdMS_TO_TICKS(50));
    esp_log_level_set("*", ESP_LOG_INFO);
    stream->started = false;
}

static esp_err_t serial_sink_begin(
    void *context, double noise_dbfs, double start_dbfs)
{
    return serial_stream_begin(
        (vad_serial_stream_t *)context, noise_dbfs, start_dbfs);
}

static esp_err_t serial_sink_write(
    void *context, const int16_t *samples, size_t sample_count)
{
    return serial_stream_write(
        (vad_serial_stream_t *)context, samples, sample_count);
}

static esp_err_t serial_sink_end(
    void *context,
    size_t sample_count,
    size_t trim_samples,
    const char *reason)
{
    return serial_stream_end(
        (vad_serial_stream_t *)context,
        sample_count,
        trim_samples,
        reason);
}

static void serial_sink_abort(void *context, const char *reason)
{
    serial_stream_abort((vad_serial_stream_t *)context, reason);
}

static esp_err_t psram_sink_begin(
    void *context, double noise_dbfs, double start_dbfs)
{
    vad_psram_stream_t *stream = (vad_psram_stream_t *)context;
    if (stream == NULL || stream->capture == NULL ||
        stream->capture->samples == NULL || stream->started) {
        return ESP_ERR_INVALID_STATE;
    }
    stream->capture->sample_count = 0U;
    stream->capture->trim_samples = 0U;
    stream->capture->noise_dbfs = noise_dbfs;
    stream->capture->start_dbfs = start_dbfs;
    stream->capture->stop_reason[0] = '\0';
    stream->started = true;
    return ESP_OK;
}

static esp_err_t psram_sink_write(
    void *context, const int16_t *samples, size_t sample_count)
{
    vad_psram_stream_t *stream = (vad_psram_stream_t *)context;
    if (stream == NULL || !stream->started || stream->capture == NULL ||
        stream->capture->samples == NULL || samples == NULL ||
        sample_count == 0U ||
        stream->capture->sample_count + sample_count >
            stream->capacity_samples) {
        return ESP_ERR_INVALID_SIZE;
    }
    memcpy(
        stream->capture->samples + stream->capture->sample_count,
        samples,
        sample_count * sizeof(*samples));
    stream->capture->sample_count += sample_count;
    return ESP_OK;
}

static esp_err_t psram_sink_end(
    void *context,
    size_t sample_count,
    size_t trim_samples,
    const char *reason)
{
    vad_psram_stream_t *stream = (vad_psram_stream_t *)context;
    if (stream == NULL || !stream->started || stream->capture == NULL ||
        reason == NULL || stream->capture->sample_count != sample_count ||
        trim_samples > sample_count) {
        return ESP_ERR_INVALID_ARG;
    }
    stream->capture->trim_samples = trim_samples;
    strlcpy(
        stream->capture->stop_reason,
        reason,
        sizeof(stream->capture->stop_reason));
    stream->started = false;
    return ESP_OK;
}

static void psram_sink_abort(void *context, const char *reason)
{
    (void)reason;
    vad_psram_stream_t *stream = (vad_psram_stream_t *)context;
    if (stream == NULL || stream->capture == NULL) {
        return;
    }
    stream->started = false;
    stream->capture->sample_count = 0U;
    stream->capture->trim_samples = 0U;
    stream->capture->stop_reason[0] = '\0';
}

static esp_err_t run_vad_capture_once(
    uint32_t sequence,
    uint32_t min_utterance_ms,
    uint32_t start_delay_ms,
    const char *profile,
    const char *display_protocol,
    nana_microphone_capture_gate_fn_t capture_gate,
    const vad_capture_sink_t *sink)
{
    if (sequence == 0U || min_utterance_ms < 100U ||
        min_utterance_ms > VAD_MAX_UTTERANCE_MS || start_delay_ms > 10000U ||
        sink == NULL || sink->begin == NULL || sink->write == NULL ||
        sink->end == NULL || sink->abort == NULL) {
        return ESP_ERR_INVALID_ARG;
    }

    emit_mic_state(sequence, "initializing");

    const uint32_t calibration_ms = vad_has_calibrated_once
                                        ? VAD_RECALIBRATION_MS
                                        : VAD_INITIAL_CALIBRATION_MS;
    const size_t calibration_frames =
        ((size_t)VAD_SAMPLE_RATE_HZ * calibration_ms) / 1000U;
    const size_t pre_roll_frames =
        ((size_t)VAD_SAMPLE_RATE_HZ * VAD_PRE_ROLL_MS) / 1000U;
    const size_t start_hold_frames =
        ((size_t)VAD_SAMPLE_RATE_HZ * VAD_START_HOLD_MS) / 1000U;
    const size_t end_silence_frames =
        ((size_t)VAD_SAMPLE_RATE_HZ * VAD_END_SILENCE_MS) / 1000U;
    const size_t keep_tail_frames =
        ((size_t)VAD_SAMPLE_RATE_HZ * VAD_KEEP_TAIL_MS) / 1000U;
    const size_t min_utterance_frames =
        ((size_t)VAD_SAMPLE_RATE_HZ * min_utterance_ms) / 1000U;
    const size_t max_utterance_frames =
        ((size_t)VAD_SAMPLE_RATE_HZ * VAD_MAX_UTTERANCE_MS) / 1000U;
    vad_stereo_frame_t frames[VAD_CHUNK_FRAMES];
    int16_t mono_frames[VAD_CHUNK_FRAMES];
    vad_level_t calibration_left = {0};
    vad_level_t calibration_right = {0};
    i2s_chan_handle_t rx_channel = NULL;
    led_strip_handle_t status_led = NULL;
    int16_t *pre_roll = NULL;
    bool channel_enabled = false;
    bool led_initialized = false;
    bool gate_cancelled = false;
    esp_err_t result = ESP_OK;

    pre_roll = heap_caps_malloc(
        pre_roll_frames * sizeof(*pre_roll), MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (pre_roll == NULL) {
        ESP_LOGE(TAG,
                 "Audio pre-roll unavailable | pre_roll=%p free=%lu largest=%zu",
                 pre_roll,
                 (unsigned long)esp_get_free_heap_size(),
                 heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL));
        result = ESP_ERR_NO_MEM;
        goto cleanup;
    }

    i2s_chan_config_t channel_config =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    // Keep the I2S RX ring small in internal DMA memory. Camera DMA uses a
    // separate small internal staging buffer while its complete JPEG frame
    // lives in PSRAM, preserving enough contiguous DMA memory for both owners.
    channel_config.dma_desc_num = VAD_DMA_DESC_NUM;
    channel_config.dma_frame_num = VAD_DMA_FRAME_NUM;

    result = i2s_new_channel(&channel_config, NULL, &rx_channel);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX allocation failed: %s", esp_err_to_name(result));
        goto cleanup;
    }

    const i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(VAD_SAMPLE_RATE_HZ),
        .slot_cfg = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(
            I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_STEREO),
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = NANA_AUDIO_BCLK_GPIO,
            .ws = NANA_AUDIO_WS_GPIO,
            .dout = I2S_GPIO_UNUSED,
            .din = NANA_AUDIO_DIN_GPIO,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv = false,
            },
        },
    };

    result = i2s_channel_init_std_mode(rx_channel, &standard_config);
    if (result != ESP_OK) {
        ESP_LOGE(TAG,
                 "I2S microphone init failed: %s | dma_ring=%uB free=%lu largest=%zu",
                 esp_err_to_name(result),
                 (unsigned)(VAD_DMA_DESC_NUM * VAD_DMA_FRAME_NUM *
                            sizeof(vad_stereo_frame_t)),
                 (unsigned long)heap_caps_get_free_size(
                     MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL),
                 heap_caps_get_largest_free_block(
                     MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL));
        goto cleanup;
    }
    result = i2s_channel_enable(rx_channel);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "I2S microphone enable failed: %s", esp_err_to_name(result));
        goto cleanup;
    }
    channel_enabled = true;

    result = init_status_led(&status_led);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Status LED init failed: %s", esp_err_to_name(result));
        goto cleanup;
    }
    led_initialized = true;
    emit_mic_state(sequence, "calibrating");

    ESP_LOGI(TAG,
             "INMP441 VAD capture | 16kHz mono PCM | BCLK=GPIO%d WS=GPIO%d DIN=GPIO%d",
             NANA_AUDIO_BCLK_GPIO,
             NANA_AUDIO_WS_GPIO,
             NANA_AUDIO_DIN_GPIO);
    ESP_LOGI(TAG,
             "Policy | calibration=%ums pre_roll=%ums start_hold=%ums stop_silence=%ums tail=%ums min=%lums max=%ums",
             calibration_ms,
             VAD_PRE_ROLL_MS,
             VAD_START_HOLD_MS,
             VAD_END_SILENCE_MS,
             VAD_KEEP_TAIL_MS,
             (unsigned long)min_utterance_ms,
             VAD_MAX_UTTERANCE_MS);
    ESP_LOGW(TAG,
             "Keep the room quiet; noise calibration starts in %lums",
             (unsigned long)start_delay_ms);
    if (!wait_with_capture_gate(start_delay_ms, capture_gate)) {
        gate_cancelled = true;
        result = ESP_ERR_INVALID_STATE;
        goto cleanup;
    }

    bool use_left = true;
    double noise_dbfs = -120.0;
    while (true) {
        calibration_left = (vad_level_t){0};
        calibration_right = (vad_level_t){0};
        size_t calibrated_frames = 0;

        while (calibrated_frames < calibration_frames) {
            if (!capture_gate_allows(capture_gate)) {
                gate_cancelled = true;
                result = ESP_ERR_INVALID_STATE;
                goto cleanup;
            }
            size_t bytes_read = 0;
            result = i2s_channel_read(rx_channel,
                                      frames,
                                      sizeof(frames),
                                      &bytes_read,
                                      pdMS_TO_TICKS(1000));
            if (result != ESP_OK) {
                ESP_LOGE(TAG, "Calibration read failed: %s", esp_err_to_name(result));
                goto cleanup;
            }
            if (bytes_read % sizeof(vad_stereo_frame_t) != 0) {
                result = ESP_ERR_INVALID_SIZE;
                goto cleanup;
            }

            const size_t frame_count = bytes_read / sizeof(vad_stereo_frame_t);
            for (size_t i = 0; i < frame_count; ++i) {
                level_add(&calibration_left, slot_to_sample24(frames[i].left));
                level_add(&calibration_right, slot_to_sample24(frames[i].right));
            }
            calibrated_frames += frame_count;
        }

        use_left = calibration_left.sum_squares >= calibration_right.sum_squares;
        const vad_level_t *active_calibration =
            use_left ? &calibration_left : &calibration_right;
        if (active_calibration->sum_squares == 0) {
            ESP_LOGE(TAG, "Both I2S channels are silent; check INMP441 wiring");
            result = ESP_ERR_INVALID_RESPONSE;
            goto cleanup;
        }

        noise_dbfs = level_dbfs(
            active_calibration->sum_squares, active_calibration->count);
        if (noise_dbfs <= VAD_CALIBRATION_MAX_NOISE_DBFS) {
            break;
        }

        ESP_LOGW(TAG,
                 "Calibration rejected | noise=%.1fdBFS is above %.1fdBFS; stay quiet and do not touch the wires",
                 noise_dbfs,
                 VAD_CALIBRATION_MAX_NOISE_DBFS);
        if (!wait_with_capture_gate(1000U, capture_gate)) {
            gate_cancelled = true;
            result = ESP_ERR_INVALID_STATE;
            goto cleanup;
        }
    }

    double start_dbfs = noise_dbfs + VAD_START_MARGIN_DB;
    if (start_dbfs < VAD_START_FLOOR_DBFS) {
        start_dbfs = VAD_START_FLOOR_DBFS;
    }
    if (start_dbfs > VAD_START_CEILING_DBFS) {
        start_dbfs = VAD_START_CEILING_DBFS;
    }
    const double end_dbfs = start_dbfs - VAD_END_HYSTERESIS_DB;

    ESP_LOGI(TAG,
             "Calibration complete | active=%s noise=%.1fdBFS start=%.1fdBFS end=%.1fdBFS",
             use_left ? "left" : "right",
             noise_dbfs,
             start_dbfs,
             end_dbfs);
    vad_has_calibrated_once = true;
    result = set_armed_led(status_led);
    if (result != ESP_OK) {
        goto cleanup;
    }
    emit_mic_state(sequence, "armed");
    emit_armed_health(sequence, profile, display_protocol);
    ESP_LOGW(TAG, "VAD ARMED | speak continuously for 3-7 seconds, then stay silent");

    size_t pre_roll_write = 0;
    size_t pre_roll_count = 0;
    size_t start_activity_frames = 0;
    size_t silence_frames = 0;
    size_t utterance_count = 0;
    bool recording = false;
    bool stopped_by_limit = false;
    size_t trim_samples = 0;
    int64_t next_health_us =
        esp_timer_get_time() + ((int64_t)VAD_HEALTH_INTERVAL_MS * 1000LL);

    while (true) {
        if (!capture_gate_allows(capture_gate)) {
            gate_cancelled = true;
            result = ESP_ERR_INVALID_STATE;
            goto cleanup;
        }
        size_t bytes_read = 0;
        result = i2s_channel_read(
            rx_channel, frames, sizeof(frames), &bytes_read, pdMS_TO_TICKS(1000));
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "VAD read failed: %s", esp_err_to_name(result));
            goto cleanup;
        }
        if (bytes_read % sizeof(vad_stereo_frame_t) != 0) {
            result = ESP_ERR_INVALID_SIZE;
            goto cleanup;
        }

        const size_t frame_count = bytes_read / sizeof(vad_stereo_frame_t);
        vad_level_t chunk_level = {0};
        for (size_t i = 0; i < frame_count; ++i) {
            const int32_t sample24 = use_left
                                         ? slot_to_sample24(frames[i].left)
                                         : slot_to_sample24(frames[i].right);
            level_add(&chunk_level, sample24);
            mono_frames[i] = sample24_to_pcm16(sample24);
            if (!recording) {
                ring_push(pre_roll,
                          pre_roll_frames,
                          &pre_roll_write,
                          &pre_roll_count,
                          mono_frames[i]);
            }
        }
        const double chunk_dbfs =
            level_dbfs(chunk_level.sum_squares, chunk_level.count);

        if (!recording) {
            const int64_t now_us = esp_timer_get_time();
            if (now_us >= next_health_us) {
                emit_armed_health(sequence, profile, display_protocol);
                next_health_us =
                    now_us + ((int64_t)VAD_HEALTH_INTERVAL_MS * 1000LL);
            }
            if (chunk_dbfs >= start_dbfs) {
                start_activity_frames += frame_count;
            } else {
                start_activity_frames = 0;
            }

            if (start_activity_frames >= start_hold_frames) {
                recording = true;
                silence_frames = 0;
                result = set_recording_led(status_led, true);
                if (result != ESP_OK) {
                    goto cleanup;
                }
                emit_mic_state(sequence, "capturing");
                ESP_LOGI(TAG,
                         "SPEECH START | trigger=%.1fdBFS pre_roll=%ums LED=on",
                         chunk_dbfs,
                         VAD_PRE_ROLL_MS);
                result = sink->begin(sink->context, noise_dbfs, start_dbfs);
                if (result != ESP_OK) {
                    goto cleanup;
                }
                result = capture_sink_ring(sink,
                                           pre_roll,
                                           pre_roll_frames,
                                           pre_roll_write,
                                           pre_roll_count);
                if (result != ESP_OK) {
                    goto cleanup;
                }
                utterance_count = pre_roll_count;
            }
            continue;
        }

        const size_t remaining_frames = max_utterance_frames - utterance_count;
        const size_t write_frames =
            frame_count < remaining_frames ? frame_count : remaining_frames;
        if (write_frames > 0U) {
            result = sink->write(sink->context, mono_frames, write_frames);
            if (result != ESP_OK) {
                goto cleanup;
            }
            utterance_count += write_frames;
        }

        if (chunk_dbfs >= end_dbfs) {
            silence_frames = 0;
        } else {
            silence_frames += frame_count;
        }

        const bool reached_limit = utterance_count >= max_utterance_frames;
        const bool reached_silence = silence_frames >= end_silence_frames;
        if (reached_limit || reached_silence) {
            stopped_by_limit = reached_limit;
            if (!stopped_by_limit && silence_frames > keep_tail_frames) {
                trim_samples = silence_frames - keep_tail_frames;
                if (trim_samples > utterance_count) {
                    trim_samples = utterance_count;
                }
            }
            break;
        }
    }

    result = set_recording_led(status_led, false);
    if (result != ESP_OK) {
        goto cleanup;
    }
    // Capture ownership ends before STREAM_END. Core cannot ACK until it sees
    // that trailer, so the speaker can never acquire I2S while RX still exists.
    if (channel_enabled) {
        result = i2s_channel_disable(rx_channel);
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "I2S microphone disable failed: %s", esp_err_to_name(result));
            goto cleanup;
        }
        channel_enabled = false;
    }
    if (rx_channel != NULL) {
        result = i2s_del_channel(rx_channel);
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "I2S microphone delete failed: %s", esp_err_to_name(result));
            goto cleanup;
        }
        rx_channel = NULL;
    }

    const size_t effective_samples = utterance_count - trim_samples;
    const char *stop_reason = stopped_by_limit
                                  ? "max_duration"
                                  : (effective_samples < min_utterance_frames
                                         ? "short"
                                         : "silence");
    result = sink->end(
        sink->context, utterance_count, trim_samples, stop_reason);
    if (result == ESP_OK) {
        ESP_LOGI(TAG,
                 "VAD capture PASS | reason=%s wire=%uB effective=%ums",
                 stop_reason,
                 (unsigned)(utterance_count * sizeof(int16_t)),
                 (unsigned)((effective_samples * 1000U) / VAD_SAMPLE_RATE_HZ));
        emit_mic_state(sequence, "suspended");
    }

cleanup:
    if (result != ESP_OK) {
        sink->abort(
            sink->context,
            gate_cancelled ? "gate_closed" : "capture_error");
    }
    if (gate_cancelled) {
        ESP_LOGI(TAG, "VAD capture cancelled before speech | gate closed");
    } else if (result != ESP_OK) {
        emit_mic_state(sequence, "error");
    }
    if (led_initialized) {
        led_strip_clear(status_led);
        led_strip_del(status_led);
    }
    if (channel_enabled) {
        i2s_channel_disable(rx_channel);
    }
    if (rx_channel != NULL) {
        i2s_del_channel(rx_channel);
    }
    free(pre_roll);
    return result;
}

esp_err_t nana_microphone_run_vad_serial_capture_once(
    uint32_t sequence,
    uint32_t min_utterance_ms,
    uint32_t start_delay_ms,
    const char *profile,
    const char *display_protocol,
    nana_microphone_capture_gate_fn_t capture_gate)
{
    vad_serial_stream_t stream = {
        .sequence = sequence,
        .crc = 0xffffffffU,
    };
    const vad_capture_sink_t sink = {
        .context = &stream,
        .begin = serial_sink_begin,
        .write = serial_sink_write,
        .end = serial_sink_end,
        .abort = serial_sink_abort,
    };
    return run_vad_capture_once(
        sequence,
        min_utterance_ms,
        start_delay_ms,
        profile,
        display_protocol,
        capture_gate,
        &sink);
}

esp_err_t nana_microphone_capture_vad_to_psram(
    uint32_t sequence,
    uint32_t min_utterance_ms,
    uint32_t start_delay_ms,
    const char *profile,
    const char *display_protocol,
    nana_microphone_capture_gate_fn_t capture_gate,
    nana_microphone_capture_t *capture)
{
    if (capture == NULL || capture->samples != NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    memset(capture, 0, sizeof(*capture));
    capture->samples = heap_caps_malloc(
        NANA_MICROPHONE_MAX_CAPTURE_BYTES,
        MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (capture->samples == NULL) {
        ESP_LOGE(
            TAG,
            "PSRAM capture buffer unavailable | requested=%uB free_psram=%zu",
            (unsigned)NANA_MICROPHONE_MAX_CAPTURE_BYTES,
            heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        return ESP_ERR_NO_MEM;
    }

    vad_psram_stream_t stream = {
        .capture = capture,
        .capacity_samples =
            NANA_MICROPHONE_MAX_CAPTURE_BYTES / sizeof(int16_t),
    };
    const vad_capture_sink_t sink = {
        .context = &stream,
        .begin = psram_sink_begin,
        .write = psram_sink_write,
        .end = psram_sink_end,
        .abort = psram_sink_abort,
    };
    const esp_err_t result = run_vad_capture_once(
        sequence,
        min_utterance_ms,
        start_delay_ms,
        profile,
        display_protocol,
        capture_gate,
        &sink);
    if (result != ESP_OK) {
        nana_microphone_capture_release(capture);
    }
    return result;
}

void nana_microphone_capture_release(nana_microphone_capture_t *capture)
{
    if (capture == NULL) {
        return;
    }
    heap_caps_free(capture->samples);
    memset(capture, 0, sizeof(*capture));
}

esp_err_t nana_microphone_run_vad_serial_capture(void)
{
    return nana_microphone_run_vad_serial_capture_once(
        1U, VAD_MIN_UTTERANCE_MS, 2000U, "mic_vad_serial_diag", "none", NULL);
}
