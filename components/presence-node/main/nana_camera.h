#pragma once

#include <stdbool.h>

#include "esp_camera.h"
#include "esp_err.h"
#include "freertos/FreeRTOS.h"

#define NANA_CAMERA_FRAME_WIDTH 640
#define NANA_CAMERA_FRAME_HEIGHT 480
#define NANA_CAMERA_MAX_JPEG_BYTES (192U * 1024U)

esp_err_t nana_camera_start(void);
esp_err_t nana_camera_stop(void);
bool nana_camera_is_started(void);
camera_fb_t *nana_camera_acquire_frame(TickType_t timeout);
void nana_camera_release_frame(camera_fb_t *frame);
bool nana_camera_frame_is_complete_jpeg(const camera_fb_t *frame);

esp_err_t nana_camera_run_serial_stream(void);
