#pragma once

#include <stdint.h>

#include "esp_err.h"

esp_err_t nana_wifi_start(void);
esp_err_t nana_wifi_start_runtime(void);
esp_err_t nana_wifi_interrupt_for_test(uint32_t duration_ms);
esp_err_t nana_wifi_wait_connected(uint32_t timeout_ms);
const char *nana_wifi_ip_address(void);
