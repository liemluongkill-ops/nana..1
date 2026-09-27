#pragma once

#include "driver/gpio.h"

/*
 * Canonical GPIO allocation for the ESP32-S3 WROOM N16R8 camera board.
 * Camera (-), SD (~), PSRAM (*), boot/strap, USB, and onboard LED pins stay
 * outside the audio bus so later peripherals do not force audio rewiring.
 */
#define NANA_AUDIO_BCLK_GPIO GPIO_NUM_41
#define NANA_AUDIO_WS_GPIO GPIO_NUM_42
#define NANA_AUDIO_DOUT_GPIO GPIO_NUM_47
#define NANA_AUDIO_DIN_GPIO GPIO_NUM_21
#define NANA_AUDIO_AMP_SD_GPIO GPIO_NUM_2

#define NANA_STATUS_LED_GPIO GPIO_NUM_48

#define NANA_CAMERA_XCLK_GPIO GPIO_NUM_15
#define NANA_CAMERA_SIOD_GPIO GPIO_NUM_4
#define NANA_CAMERA_SIOC_GPIO GPIO_NUM_5
#define NANA_CAMERA_D0_GPIO GPIO_NUM_11
#define NANA_CAMERA_D1_GPIO GPIO_NUM_9
#define NANA_CAMERA_D2_GPIO GPIO_NUM_8
#define NANA_CAMERA_D3_GPIO GPIO_NUM_10
#define NANA_CAMERA_D4_GPIO GPIO_NUM_12
#define NANA_CAMERA_D5_GPIO GPIO_NUM_18
#define NANA_CAMERA_D6_GPIO GPIO_NUM_17
#define NANA_CAMERA_D7_GPIO GPIO_NUM_16
#define NANA_CAMERA_VSYNC_GPIO GPIO_NUM_6
#define NANA_CAMERA_HREF_GPIO GPIO_NUM_7
#define NANA_CAMERA_PCLK_GPIO GPIO_NUM_13

#define NANA_SD_CMD_GPIO GPIO_NUM_38
#define NANA_SD_CLK_GPIO GPIO_NUM_39
#define NANA_SD_DATA0_GPIO GPIO_NUM_40

/*
 * Temporary ST7789 bring-up wiring on the camera board. The display borrows
 * the unused SD bus for this bench test, so the microSD card must be removed.
 * Final Presence Node display pins belong on the non-camera N16R8 board.
 */
#define NANA_DISPLAY_MOSI_GPIO NANA_SD_CMD_GPIO
#define NANA_DISPLAY_SCLK_GPIO NANA_SD_CLK_GPIO
#define NANA_DISPLAY_DC_GPIO NANA_SD_DATA0_GPIO
#define NANA_DISPLAY_CS_GPIO GPIO_NUM_14
#define NANA_DISPLAY_RESET_GPIO GPIO_NUM_1
