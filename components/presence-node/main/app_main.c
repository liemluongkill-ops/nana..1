#include <inttypes.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdbool.h>

#include "driver/i2s_std.h"
#include "esp_chip_info.h"
#include "esp_flash.h"
#include "esp_heap_caps.h"
#include "esp_idf_version.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_psram.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "led_strip.h"
#include "led_strip_rmt.h"

#include "nana_board_pins.h"
#include "nana_camera.h"
#include "nana_camera_http.h"
#include "nana_display.h"
#include "nana_microphone_vad.h"
#include "nana_presence_session.h"
#include "nana_speaker.h"
#include "nana_wifi.h"

#define SPEAKER_SAMPLE_RATE_HZ 44100U
// Start at half scale after power-rail changes; raise only after a clean smoke.
#define VOICE_GAIN_Q15 16384
#define VOICE_GAIN_PERCENT 50U

#define MICROPHONE_SAMPLE_RATE_HZ 16000U
#define MICROPHONE_CHUNK_FRAMES 256U
#define MICROPHONE_REPORT_MS 250U
#define MICROPHONE_TEST_DURATION_MS 10000U
#define STATUS_LED_RECORDING_RED 0U
#define STATUS_LED_RECORDING_GREEN 12U
#define STATUS_LED_RECORDING_BLUE 24U
typedef enum {
    NANA_BRINGUP_CAMERA_LAN,
    NANA_BRINGUP_CAMERA_SERIAL,
    NANA_BRINGUP_MICROPHONE,
    NANA_BRINGUP_MICROPHONE_VAD_SERIAL,
    NANA_BRINGUP_SPEAKER,
    NANA_BRINGUP_DISPLAY,
    NANA_BRINGUP_PRESENCE_SESSION,
} nana_bringup_mode_t;

static const nana_bringup_mode_t NANA_BRINGUP_MODE =
    NANA_BRINGUP_PRESENCE_SESSION;

typedef struct {
    int32_t left;
    int32_t right;
} stereo_rx_frame_t;

typedef struct {
    int64_t sum;
    uint64_t sum_squares;
    int32_t peak;
    uint32_t clipped_samples;
    size_t sample_count;
} microphone_level_t;

static const char *TAG = "nana_bringup";
static stereo_rx_frame_t microphone_frames[MICROPHONE_CHUNK_FRAMES];
static led_strip_handle_t status_led = NULL;

extern const uint8_t nana_voice_test_pcm_start[]
    asm("_binary_nana_voice_test_pcm_start");
extern const uint8_t nana_voice_test_pcm_end[]
    asm("_binary_nana_voice_test_pcm_end");

static void log_mac_address(void)
{
    uint8_t mac[6] = {0};

    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) == ESP_OK) {
        ESP_LOGI(TAG, "Wi-Fi MAC: %02x:%02x:%02x:%02x:%02x:%02x",
                 mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
    } else {
        ESP_LOGW(TAG, "Wi-Fi MAC unavailable");
    }
}

static void log_hardware_summary(void)
{
    esp_chip_info_t chip_info = {0};
    uint32_t flash_size = 0;
    const size_t psram_total = esp_psram_get_size();

    esp_chip_info(&chip_info);

    ESP_LOGI(TAG, "Nana Presence Node bring-up");
    ESP_LOGI(TAG, "ESP-IDF: %s", esp_get_idf_version());
    ESP_LOGI(TAG, "Chip: ESP32-S3 | cores=%u | revision=%u.%u",
             chip_info.cores,
             chip_info.revision / 100,
             chip_info.revision % 100);

    if (esp_flash_get_size(NULL, &flash_size) == ESP_OK) {
        ESP_LOGI(TAG, "Flash: %" PRIu32 " bytes (%" PRIu32 " MiB)",
                 flash_size, flash_size / (1024U * 1024U));
    } else {
        ESP_LOGW(TAG, "Flash size unavailable");
    }

    if (!esp_psram_is_initialized() || psram_total == 0) {
        ESP_LOGW(TAG, "PSRAM unavailable; control session remains supported");
    } else {
        ESP_LOGI(TAG, "PSRAM: detected=%zu bytes | access=memory_map", psram_total);
    }
    ESP_LOGI(TAG, "Internal heap: total=%zu bytes | free=%zu bytes",
             heap_caps_get_total_size(MALLOC_CAP_INTERNAL),
             heap_caps_get_free_size(MALLOC_CAP_INTERNAL));
    ESP_LOGI(TAG, "Reset reason: %d", esp_reset_reason());
    log_mac_address();
}

static void microphone_level_add(microphone_level_t *level, int32_t sample)
{
    const int64_t sample_64 = sample;
    const int32_t magnitude = sample < 0 ? -sample : sample;

    level->sum += sample_64;
    level->sum_squares += (uint64_t)(sample_64 * sample_64);
    if (magnitude > level->peak) {
        level->peak = magnitude;
    }
    if (magnitude >= 8388600) {
        ++level->clipped_samples;
    }
    ++level->sample_count;
}

static double microphone_dbfs(uint64_t sum_squares, size_t sample_count)
{
    if (sum_squares == 0 || sample_count == 0) {
        return -120.0;
    }

    const double rms = sqrt((double)sum_squares / (double)sample_count);
    return 20.0 * log10(rms / 8388607.0);
}

static double microphone_peak_dbfs(int32_t peak)
{
    if (peak <= 0) {
        return -120.0;
    }
    return 20.0 * log10((double)peak / 8388607.0);
}

static esp_err_t init_status_led(void)
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

    esp_err_t result = led_strip_new_rmt_device(
        &strip_config, &rmt_config, &status_led);
    if (result != ESP_OK) {
        return result;
    }
    return led_strip_clear(status_led);
}

static esp_err_t set_recording_led(bool enabled)
{
    if (status_led == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    if (enabled) {
        esp_err_t result = led_strip_set_pixel(
            status_led,
            0,
            STATUS_LED_RECORDING_RED,
            STATUS_LED_RECORDING_GREEN,
            STATUS_LED_RECORDING_BLUE);
        if (result != ESP_OK) {
            return result;
        }
        return led_strip_refresh(status_led);
    }
    return led_strip_clear(status_led);
}

static esp_err_t run_microphone_smoke(void)
{
    i2s_chan_handle_t rx_channel = NULL;
    i2s_chan_config_t channel_config =
        I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    microphone_level_t left_level = {0};
    microphone_level_t right_level = {0};
    uint64_t total_left_energy = 0;
    uint64_t total_right_energy = 0;
    size_t total_frames = 0;
    const size_t report_frames =
        ((size_t)MICROPHONE_SAMPLE_RATE_HZ * MICROPHONE_REPORT_MS) / 1000U;
    const size_t target_frames =
        ((size_t)MICROPHONE_SAMPLE_RATE_HZ * MICROPHONE_TEST_DURATION_MS) / 1000U;

    channel_config.dma_desc_num = 8;
    channel_config.dma_frame_num = MICROPHONE_CHUNK_FRAMES;

    ESP_LOGI(TAG,
             "INMP441 wiring: VCC=3V3 GND=GND SCK=GPIO%d WS=GPIO%d SD=GPIO%d L/R=GND",
             NANA_AUDIO_BCLK_GPIO,
             NANA_AUDIO_WS_GPIO,
             NANA_AUDIO_DIN_GPIO);
    ESP_LOGI(TAG,
             "Microphone smoke: %uHz | 24-bit I2S in 32-bit stereo slots | %ums",
             MICROPHONE_SAMPLE_RATE_HZ,
             MICROPHONE_TEST_DURATION_MS);
    ESP_LOGI(TAG,
             "Main task stack high-water before I2S init: %u bytes",
             (unsigned)uxTaskGetStackHighWaterMark(NULL));

    esp_err_t result = init_status_led();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "GPIO%d status LED init failed: %s",
                 NANA_STATUS_LED_GPIO, esp_err_to_name(result));
        return result;
    }

    result = i2s_new_channel(&channel_config, NULL, &rx_channel);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "I2S RX channel allocation failed: %s", esp_err_to_name(result));
        return result;
    }

    const i2s_std_config_t standard_config = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(MICROPHONE_SAMPLE_RATE_HZ),
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
        ESP_LOGE(TAG, "I2S microphone init failed: %s", esp_err_to_name(result));
        i2s_del_channel(rx_channel);
        return result;
    }

    result = i2s_channel_enable(rx_channel);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "I2S microphone enable failed: %s", esp_err_to_name(result));
        i2s_del_channel(rx_channel);
        return result;
    }

    ESP_LOGW(TAG, "Microphone capture starts in 2 seconds; speak and clap during the test");
    vTaskDelay(pdMS_TO_TICKS(2000));

    result = set_recording_led(true);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Recording LED enable failed: %s", esp_err_to_name(result));
        i2s_channel_disable(rx_channel);
        i2s_del_channel(rx_channel);
        return result;
    }
    ESP_LOGI(TAG,
             "REC START | LED=on GPIO%d | window=%ums",
             NANA_STATUS_LED_GPIO,
             MICROPHONE_TEST_DURATION_MS);

    while (total_frames < target_frames) {
        size_t bytes_read = 0;
        result = i2s_channel_read(
            rx_channel,
            microphone_frames,
            sizeof(microphone_frames),
            &bytes_read,
            pdMS_TO_TICKS(1000));
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "I2S microphone read failed: %s", esp_err_to_name(result));
            break;
        }
        if (bytes_read % sizeof(stereo_rx_frame_t) != 0) {
            result = ESP_ERR_INVALID_SIZE;
            ESP_LOGE(TAG, "I2S microphone returned a partial stereo frame");
            break;
        }

        const size_t frames_read = bytes_read / sizeof(stereo_rx_frame_t);
        for (size_t i = 0; i < frames_read; ++i) {
            // INMP441 places its signed 24-bit sample in the high bits of each slot.
            microphone_level_add(&left_level, microphone_frames[i].left >> 8);
            microphone_level_add(&right_level, microphone_frames[i].right >> 8);
        }
        total_frames += frames_read;

        if (left_level.sample_count >= report_frames) {
            const bool left_active = left_level.sum_squares >= right_level.sum_squares;
            const microphone_level_t *active = left_active ? &left_level : &right_level;
            const double dc = active->sample_count == 0
                                  ? 0.0
                                  : (double)active->sum / (double)active->sample_count;

            ESP_LOGI(TAG,
                     "MIC level | active=%s | rms=%.1fdBFS | peak=%.1fdBFS | dc=%.0f | clipped=%" PRIu32,
                     left_active ? "left" : "right",
                     microphone_dbfs(active->sum_squares, active->sample_count),
                     microphone_peak_dbfs(active->peak),
                     dc,
                     active->clipped_samples);

            total_left_energy += left_level.sum_squares;
            total_right_energy += right_level.sum_squares;
            left_level = (microphone_level_t){0};
            right_level = (microphone_level_t){0};
        }
    }

    const esp_err_t led_off_result = set_recording_led(false);
    ESP_LOGI(TAG,
             "REC STOP | LED=off GPIO%d | captured_frames=%zu",
             NANA_STATUS_LED_GPIO,
             total_frames);

    total_left_energy += left_level.sum_squares;
    total_right_energy += right_level.sum_squares;

    const esp_err_t disable_result = i2s_channel_disable(rx_channel);
    const esp_err_t delete_result = i2s_del_channel(rx_channel);
    const esp_err_t led_delete_result = led_strip_del(status_led);
    status_led = NULL;
    if (result == ESP_OK && led_off_result != ESP_OK) {
        result = led_off_result;
    }
    if (result == ESP_OK && disable_result != ESP_OK) {
        result = disable_result;
    }
    if (result == ESP_OK && delete_result != ESP_OK) {
        result = delete_result;
    }
    if (result == ESP_OK && led_delete_result != ESP_OK) {
        result = led_delete_result;
    }

    if (result == ESP_OK && total_left_energy == 0 && total_right_energy == 0) {
        result = ESP_ERR_INVALID_RESPONSE;
        ESP_LOGE(TAG, "Microphone smoke failed: both I2S channels were all zero");
    } else if (result == ESP_OK) {
        ESP_LOGI(TAG,
                 "Microphone smoke: PASS | frames=%zu | detected_channel=%s",
                 total_frames,
                 total_left_energy >= total_right_energy ? "left" : "right");
    }

    return result;
}

static esp_err_t run_voice_sample_smoke(void)
{
    const size_t pcm_size =
        (size_t)(nana_voice_test_pcm_end - nana_voice_test_pcm_start);
    const uint32_t duration_ms =
        (uint32_t)(((pcm_size / sizeof(int16_t)) * 1000U) /
                   SPEAKER_SAMPLE_RATE_HZ);

    ESP_LOGI(TAG,
             "MAX98357 wiring: VIN=5V GND=GND BCLK=GPIO%d LRC=GPIO%d DIN=GPIO%d SD=GPIO%d",
             NANA_AUDIO_BCLK_GPIO,
             NANA_AUDIO_WS_GPIO,
             NANA_AUDIO_DOUT_GPIO,
             NANA_AUDIO_AMP_SD_GPIO);
    ESP_LOGI(TAG, "Reserved microphone data pin: GPIO%d", NANA_AUDIO_DIN_GPIO);
    ESP_LOGI(TAG,
             "Embedded Nana voice: %zu bytes | %" PRIu32
             "ms | mono s16le | gain=%u%%",
             pcm_size,
             duration_ms,
             VOICE_GAIN_PERCENT);

    ESP_LOGW(TAG, "Nana voice sample starts in 2 seconds");
    vTaskDelay(pdMS_TO_TICKS(2000));
    const esp_err_t result = nana_speaker_play_pcm16_mono(
        (const int16_t *)nana_voice_test_pcm_start,
        pcm_size / sizeof(int16_t),
        SPEAKER_SAMPLE_RATE_HZ,
        VOICE_GAIN_Q15);

    if (result == ESP_OK) {
        ESP_LOGI(TAG, "Nana voice smoke: PASS (6-second sample transmitted once)");
    } else {
        ESP_LOGE(TAG, "Nana voice smoke failed: %s", esp_err_to_name(result));
    }
    return result;
}

void app_main(void)
{
    // IDF maps PSRAM during startup; the control session does not depend on it.
    esp_err_t result = ESP_OK;
    if (result == ESP_OK) {
        result = nana_speaker_init();
    }
    log_hardware_summary();
    if (result == ESP_OK) {
        switch (NANA_BRINGUP_MODE) {
        case NANA_BRINGUP_CAMERA_LAN:
            result = nana_wifi_start();
            if (result == ESP_OK) {
                result = nana_camera_http_start(nana_wifi_ip_address());
            }
            break;
        case NANA_BRINGUP_CAMERA_SERIAL:
            result = nana_camera_run_serial_stream();
            break;
        case NANA_BRINGUP_MICROPHONE:
            result = run_microphone_smoke();
            break;
        case NANA_BRINGUP_MICROPHONE_VAD_SERIAL:
            result = nana_microphone_run_vad_serial_capture();
            break;
        case NANA_BRINGUP_SPEAKER:
            result = run_voice_sample_smoke();
            break;
        case NANA_BRINGUP_DISPLAY:
            result = nana_display_run_face_controller();
            break;
        case NANA_BRINGUP_PRESENCE_SESSION:
            ESP_LOGI(TAG,
                     "Presence session profile: LAN control + bounded half-duplex PCM + display");
            result = nana_speaker_mute();
            if (result == ESP_OK) {
                result = nana_display_start_face_controller();
            }
            if (result == ESP_OK) {
                result = nana_wifi_start_runtime();
            }
            if (result == ESP_OK) {
                result = nana_presence_session_run();
            }
            break;
        }
    }

    if (result != ESP_OK) {
        const esp_err_t mute_result = nana_speaker_mute();
        if (mute_result != ESP_OK) {
            ESP_LOGE(TAG,
                     "Fail-safe speaker mute failed: %s",
                     esp_err_to_name(mute_result));
        }
        ESP_LOGE(TAG, "Bring-up service failed: %s", esp_err_to_name(result));
    }

    while (true) {
        const int64_t uptime_ms = esp_timer_get_time() / 1000;
        ESP_LOGI(TAG,
                 "heartbeat | uptime=%" PRId64 "ms | free_heap=%" PRIu32 " | min_heap=%" PRIu32,
                 uptime_ms,
                 esp_get_free_heap_size(),
                 esp_get_minimum_free_heap_size());
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
