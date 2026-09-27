#pragma once

#include <stdbool.h>

#include "esp_err.h"

esp_err_t nana_camera_http_start(const char *ip_address);
bool nana_camera_http_audio_allowed(void);
