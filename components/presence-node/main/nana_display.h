#pragma once

#include <stdbool.h>

#include "esp_err.h"

typedef enum {
    NANA_FACE_IDLE,
    NANA_FACE_LISTENING,
    NANA_FACE_PONDERING,
    NANA_FACE_HAPPY,
    NANA_FACE_LAUGHING,
    NANA_FACE_GLEE,
    NANA_FACE_SLEEPING,
    NANA_FACE_SURPRISED,
    NANA_FACE_SHOCKED,
    NANA_FACE_SCARED,
    NANA_FACE_AWE,
    NANA_FACE_SKEPTICAL,
    NANA_FACE_SQUINT,
    NANA_FACE_UNCLEAR,
    NANA_FACE_THINKING,
    NANA_FACE_CURIOUS,
    NANA_FACE_PLAYFUL,
    NANA_FACE_SHY,
    NANA_FACE_RELIEVED,
    NANA_FACE_PLEADING,
    NANA_FACE_LOVE,
    NANA_FACE_DIZZY,
    NANA_FACE_PROUD,
    NANA_FACE_WINK_LEFT,
    NANA_FACE_WINK_RIGHT,
    NANA_FACE_DOUBLE_BLINK,
    NANA_FACE_GLANCE_LEFT,
    NANA_FACE_GLANCE_RIGHT,
    NANA_FACE_GLANCE_UP,
    NANA_FACE_GLANCE_DOWN,
    NANA_FACE_LOOK_AROUND,
    NANA_FACE_SCAN,
    NANA_FACE_PEEK_LEFT,
    NANA_FACE_PEEK_RIGHT,
    NANA_FACE_STARTLED_BLINK,
    NANA_FACE_DOUBLE_TAKE,
    NANA_FACE_CELEBRATE,
    NANA_FACE_PANIC,
    NANA_FACE_STATE_COUNT,
} nana_face_state_t;

typedef enum {
    NANA_FACE_TAG_EXPRESSION,
    NANA_FACE_TAG_GESTURE,
} nana_face_tag_kind_t;

const char *nana_face_state_tag(nana_face_state_t state);
bool nana_face_state_from_tag(const char *tag, nana_face_state_t *state);
nana_face_tag_kind_t nana_face_state_tag_kind(nana_face_state_t state);
bool nana_face_tag_is_gesture(const char *tag);
bool nana_display_set_expression(const char *tag);
bool nana_display_trigger_event(const char *tag);
bool nana_display_is_running(void);
esp_err_t nana_display_start_face_controller(void);
esp_err_t nana_display_run_face_controller(void);
