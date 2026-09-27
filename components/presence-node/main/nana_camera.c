#include <errno.h>
#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "driver/sdmmc_host.h"
#include "esp_camera.h"
#include "esp_crc.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_vfs_fat.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "sdmmc_cmd.h"
#include "sensor.h"

#include "nana_board_pins.h"
#include "nana_camera.h"

#define NANA_CAMERA_XCLK_HZ 20000000
#define NANA_CAMERA_WARMUP_FRAMES 2
#define NANA_CAMERA_SD_MOUNT_POINT "/sdcard"
#define NANA_CAMERA_CAPTURE_PATH "/sdcard/NANA.JPG"
#define NANA_CAMERA_SERIAL_INTERVAL_MS 1000
#define NANA_CAMERA_SERIAL_B64_CAPACITY (96U * 1024U)

static const char *TAG = "nana_camera";
static SemaphoreHandle_t camera_mutex = NULL;
static bool camera_started = false;

#if defined(CONFIG_CAMERA_PSRAM_DMA)
#error "Direct camera PSRAM DMA corrupts OV5640 JPEGs on this board; use staged DMA"
#endif

#if !defined(CONFIG_CAMERA_JPEG_MODE_FRAME_SIZE_CUSTOM)
#error "Presence camera requires a bounded custom JPEG framebuffer"
#endif

_Static_assert(
    CONFIG_CAMERA_JPEG_MODE_FRAME_SIZE == NANA_CAMERA_MAX_JPEG_BYTES,
    "Camera driver and Presence transport JPEG bounds must match");

bool nana_camera_frame_is_complete_jpeg(const camera_fb_t *frame)
{
    return frame != NULL &&
           frame->format == PIXFORMAT_JPEG &&
           frame->len >= 4 &&
           frame->buf[0] == 0xff &&
           frame->buf[1] == 0xd8 &&
           frame->buf[frame->len - 2] == 0xff &&
           frame->buf[frame->len - 1] == 0xd9;
}

static esp_err_t ensure_camera_mutex(void)
{
    if (camera_mutex != NULL) {
        return ESP_OK;
    }

    camera_mutex = xSemaphoreCreateMutex();
    return camera_mutex == NULL ? ESP_ERR_NO_MEM : ESP_OK;
}

static esp_err_t save_frame_to_sd(const camera_fb_t *frame)
{
    const esp_vfs_fat_sdmmc_mount_config_t mount_config = {
        .format_if_mount_failed = false,
        .max_files = 2,
        .allocation_unit_size = 16 * 1024,
    };
    sdmmc_host_t host = SDMMC_HOST_DEFAULT();
    sdmmc_slot_config_t slot_config = SDMMC_SLOT_CONFIG_DEFAULT();
    sdmmc_card_t *card = NULL;

    slot_config.width = 1;
    slot_config.clk = NANA_SD_CLK_GPIO;
    slot_config.cmd = NANA_SD_CMD_GPIO;
    slot_config.d0 = NANA_SD_DATA0_GPIO;
    slot_config.flags |= SDMMC_SLOT_FLAG_INTERNAL_PULLUP;

    esp_err_t result = esp_vfs_fat_sdmmc_mount(
        NANA_CAMERA_SD_MOUNT_POINT,
        &host,
        &slot_config,
        &mount_config,
        &card);
    if (result != ESP_OK) {
        ESP_LOGW(TAG, "SD export skipped: mount failed: %s",
                 esp_err_to_name(result));
        return result;
    }

    FILE *file = fopen(NANA_CAMERA_CAPTURE_PATH, "wb");
    if (file == NULL) {
        ESP_LOGW(TAG, "SD export failed: cannot open %s: errno=%d (%s)",
                 NANA_CAMERA_CAPTURE_PATH,
                 errno,
                 strerror(errno));
        esp_vfs_fat_sdcard_unmount(NANA_CAMERA_SD_MOUNT_POINT, card);
        return ESP_FAIL;
    }

    const size_t written = fwrite(frame->buf, 1, frame->len, file);
    const int close_result = fclose(file);
    if (written != frame->len || close_result != 0) {
        ESP_LOGW(TAG, "SD export failed: wrote %zu/%zu bytes",
                 written, frame->len);
        result = ESP_FAIL;
    } else {
        ESP_LOGI(TAG, "SD export: PASS | path=%s | bytes=%zu",
                 NANA_CAMERA_CAPTURE_PATH, written);
    }

    const esp_err_t unmount_result =
        esp_vfs_fat_sdcard_unmount(NANA_CAMERA_SD_MOUNT_POINT, card);
    if (result == ESP_OK && unmount_result != ESP_OK) {
        result = unmount_result;
    }
    return result;
}

static esp_err_t send_frame_to_serial(const camera_fb_t *frame,
                                      unsigned char *encoded,
                                      size_t encoded_capacity,
                                      uint32_t sequence)
{
    size_t encoded_length = 0;
    const int encode_result = mbedtls_base64_encode(
        encoded,
        encoded_capacity,
        &encoded_length,
        frame->buf,
        frame->len);
    if (encode_result != 0) {
        ESP_LOGE(TAG,
                 "Base64 encode failed: result=%d frame_bytes=%zu capacity=%zu",
                 encode_result,
                 frame->len,
                 encoded_capacity);
        return ESP_ERR_INVALID_SIZE;
    }

    const uint32_t crc32 = esp_crc32_le(0, frame->buf, frame->len);
    clearerr(stdout);
    printf("NANA_FRAME_BEGIN seq=%" PRIu32
           " bytes=%zu base64=%zu width=%zu height=%zu crc32=%08" PRIx32 "\n",
           sequence,
           frame->len,
           encoded_length,
           frame->width,
           frame->height,
           crc32);
    const size_t written = fwrite(encoded, 1, encoded_length, stdout);
    printf("\nNANA_FRAME_END seq=%" PRIu32 "\n", sequence);
    fflush(stdout);

    if (written != encoded_length || ferror(stdout)) {
        ESP_LOGE(TAG,
                 "Serial frame write failed: wrote=%zu/%zu",
                 written,
                 encoded_length);
        return ESP_FAIL;
    }
    return ESP_OK;
}

static camera_config_t camera_config(void)
{
    return (camera_config_t){
        .pin_pwdn = -1,
        .pin_reset = -1,
        .pin_xclk = NANA_CAMERA_XCLK_GPIO,
        .pin_sccb_sda = NANA_CAMERA_SIOD_GPIO,
        .pin_sccb_scl = NANA_CAMERA_SIOC_GPIO,
        .pin_d7 = NANA_CAMERA_D7_GPIO,
        .pin_d6 = NANA_CAMERA_D6_GPIO,
        .pin_d5 = NANA_CAMERA_D5_GPIO,
        .pin_d4 = NANA_CAMERA_D4_GPIO,
        .pin_d3 = NANA_CAMERA_D3_GPIO,
        .pin_d2 = NANA_CAMERA_D2_GPIO,
        .pin_d1 = NANA_CAMERA_D1_GPIO,
        .pin_d0 = NANA_CAMERA_D0_GPIO,
        .pin_vsync = NANA_CAMERA_VSYNC_GPIO,
        .pin_href = NANA_CAMERA_HREF_GPIO,
        .pin_pclk = NANA_CAMERA_PCLK_GPIO,
        .xclk_freq_hz = NANA_CAMERA_XCLK_HZ,
        .ledc_timer = LEDC_TIMER_0,
        .ledc_channel = LEDC_CHANNEL_0,
        .pixel_format = PIXFORMAT_JPEG,
        .frame_size = FRAMESIZE_VGA,
        .jpeg_quality = 12,
        .fb_count = 1,
        .fb_location = CAMERA_FB_IN_PSRAM,
        .grab_mode = CAMERA_GRAB_WHEN_EMPTY,
    };
}

esp_err_t nana_camera_start(void)
{
    const camera_config_t config = camera_config();

    if (camera_started) {
        return ESP_OK;
    }

    esp_err_t result = ensure_camera_mutex();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Camera mutex allocation failed: %s",
                 esp_err_to_name(result));
        return result;
    }

    ESP_LOGI(TAG,
             "Camera: VGA JPEG | one 192 KiB PSRAM framebuffer | staged DMA");
    ESP_LOGI(TAG,
             "Camera control: XCLK=%d SIOD=%d SIOC=%d VSYNC=%d HREF=%d PCLK=%d",
             NANA_CAMERA_XCLK_GPIO,
             NANA_CAMERA_SIOD_GPIO,
             NANA_CAMERA_SIOC_GPIO,
             NANA_CAMERA_VSYNC_GPIO,
             NANA_CAMERA_HREF_GPIO,
             NANA_CAMERA_PCLK_GPIO);
    ESP_LOGI(TAG,
             "Camera data: D0=%d D1=%d D2=%d D3=%d D4=%d D5=%d D6=%d D7=%d",
             NANA_CAMERA_D0_GPIO,
             NANA_CAMERA_D1_GPIO,
             NANA_CAMERA_D2_GPIO,
             NANA_CAMERA_D3_GPIO,
             NANA_CAMERA_D4_GPIO,
             NANA_CAMERA_D5_GPIO,
             NANA_CAMERA_D6_GPIO,
             NANA_CAMERA_D7_GPIO);

    result = esp_camera_init(&config);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Camera init failed: %s (0x%x)",
                 esp_err_to_name(result), (unsigned)result);
        return result;
    }

    sensor_t *sensor = esp_camera_sensor_get();
    if (sensor == NULL) {
        ESP_LOGE(TAG, "Camera initialized without a sensor handle");
        esp_camera_deinit();
        return ESP_ERR_INVALID_RESPONSE;
    }

    ESP_LOGI(TAG,
             "Sensor detected: PID=0x%04x VER=0x%02x MIDH=0x%02x MIDL=0x%02x",
             sensor->id.PID,
             sensor->id.VER,
             sensor->id.MIDH,
             sensor->id.MIDL);
    if (sensor->id.PID != OV5640_PID) {
        ESP_LOGE(TAG, "Unexpected camera sensor; expected OV5640 PID=0x%04x",
                 OV5640_PID);
        esp_camera_deinit();
        return ESP_ERR_INVALID_RESPONSE;
    }

    if (sensor->set_vflip(sensor, 1) != 0) {
        ESP_LOGE(TAG, "Camera vertical orientation setup failed");
        esp_camera_deinit();
        return ESP_FAIL;
    }
    ESP_LOGI(TAG, "Camera orientation: vertical flip enabled");

    for (int i = 0; i < NANA_CAMERA_WARMUP_FRAMES; ++i) {
        camera_fb_t *frame = esp_camera_fb_get();
        if (frame == NULL) {
            ESP_LOGE(TAG, "Frame capture %d failed", i + 1);
            esp_camera_deinit();
            return ESP_ERR_INVALID_RESPONSE;
        }
        esp_camera_fb_return(frame);
    }

    camera_started = true;
    ESP_LOGI(TAG, "Camera start: PASS | sensor=OV5640 | capture=VGA JPEG");
    return ESP_OK;
}

bool nana_camera_is_started(void)
{
    return camera_started;
}

esp_err_t nana_camera_stop(void)
{
    if (!camera_started) {
        return ESP_OK;
    }
    if (camera_mutex == NULL ||
        xSemaphoreTake(camera_mutex, pdMS_TO_TICKS(2000U)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    const esp_err_t result = esp_camera_deinit();
    if (result == ESP_OK) {
        camera_started = false;
        ESP_LOGI(TAG, "Camera stop: PASS");
    }
    xSemaphoreGive(camera_mutex);
    return result;
}

camera_fb_t *nana_camera_acquire_frame(TickType_t timeout)
{
    if (!camera_started || camera_mutex == NULL) {
        return NULL;
    }
    if (xSemaphoreTake(camera_mutex, timeout) != pdTRUE) {
        return NULL;
    }

    camera_fb_t *frame = esp_camera_fb_get();
    if (frame == NULL) {
        xSemaphoreGive(camera_mutex);
    }
    return frame;
}

void nana_camera_release_frame(camera_fb_t *frame)
{
    if (frame != NULL) {
        esp_camera_fb_return(frame);
    }
    if (camera_mutex != NULL) {
        xSemaphoreGive(camera_mutex);
    }
}

esp_err_t nana_camera_run_serial_stream(void)
{
    const size_t free_before = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    esp_err_t result = nana_camera_start();
    if (result != ESP_OK) {
        return result;
    }

    camera_fb_t *frame = nana_camera_acquire_frame(pdMS_TO_TICKS(2000));
    if (frame == NULL) {
        ESP_LOGE(TAG, "Initial serial frame capture failed");
        return ESP_ERR_TIMEOUT;
    }

    result = nana_camera_frame_is_complete_jpeg(frame)
                 ? ESP_OK
                 : ESP_ERR_INVALID_RESPONSE;
    ESP_LOGI(TAG,
             "Frame: %zux%zu | bytes=%zu | format=%d | jpeg_complete=%s",
             frame->width,
             frame->height,
             frame->len,
             frame->format,
             result == ESP_OK ? "true" : "false");
    if (result == ESP_OK) {
        const esp_err_t export_result = save_frame_to_sd(frame);
        if (export_result != ESP_OK) {
            ESP_LOGW(TAG,
                     "Camera capture passed; visual SD export remains unverified");
        }
    }

    unsigned char *encoded = NULL;
    if (result == ESP_OK) {
        encoded = heap_caps_malloc(
            NANA_CAMERA_SERIAL_B64_CAPACITY,
            MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
        if (encoded == NULL) {
            ESP_LOGE(TAG, "Serial frame buffer allocation failed");
            result = ESP_ERR_NO_MEM;
        }
    }

    uint32_t sequence = 1;
    if (result == ESP_OK) {
        ESP_LOGI(TAG,
                 "Camera smoke: PASS | sensor=OV5640 | capture=VGA JPEG");
        ESP_LOGI(TAG,
                 "Serial camera stream: START | interval=%dms | port=USB-UART",
                 NANA_CAMERA_SERIAL_INTERVAL_MS);
    }

    while (result == ESP_OK) {
        if (!nana_camera_frame_is_complete_jpeg(frame)) {
            ESP_LOGE(TAG, "Serial frame %" PRIu32 " is not a complete JPEG",
                     sequence);
            result = ESP_ERR_INVALID_RESPONSE;
        } else {
            result = send_frame_to_serial(
                frame,
                encoded,
                NANA_CAMERA_SERIAL_B64_CAPACITY,
                sequence);
        }

        nana_camera_release_frame(frame);
        frame = NULL;
        if (result != ESP_OK) {
            break;
        }

        ++sequence;
        vTaskDelay(pdMS_TO_TICKS(NANA_CAMERA_SERIAL_INTERVAL_MS));
        frame = nana_camera_acquire_frame(pdMS_TO_TICKS(2000));
        if (frame == NULL) {
            ESP_LOGE(TAG, "Serial frame capture %" PRIu32 " failed", sequence);
            result = ESP_ERR_INVALID_RESPONSE;
        }
    }

    if (frame != NULL) {
        nana_camera_release_frame(frame);
    }
    free(encoded);

    const size_t free_after = heap_caps_get_free_size(MALLOC_CAP_INTERNAL);
    ESP_LOGI(TAG,
             "Internal heap: before=%zu | after=%zu | delta=%td",
             free_before,
             free_after,
             (ptrdiff_t)free_after - (ptrdiff_t)free_before);

    ESP_LOGE(TAG, "Camera serial diagnostic stopped: %s",
             esp_err_to_name(result));
    return result;
}
