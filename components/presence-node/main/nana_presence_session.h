#pragma once

#include "esp_err.h"

/* Run authenticated control plus bounded PCM16/16 kHz mono downlink. */
esp_err_t nana_presence_session_run(void);
