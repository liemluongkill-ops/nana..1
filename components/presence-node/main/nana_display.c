#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

#include "driver/spi_master.h"
#include "esp_heap_caps.h"
#include "esp_lcd_panel_io.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_vendor.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "nana_board_pins.h"
#include "nana_display.h"

#define DISPLAY_HOST SPI2_HOST
#define DISPLAY_WIDTH 280
#define DISPLAY_HEIGHT 240
#define DISPLAY_X_GAP 20
#define DISPLAY_STRIPE_HEIGHT 20
#define DISPLAY_PIXEL_CLOCK_HZ (40U * 1000U * 1000U)
#define DISPLAY_FRAME_INTERVAL_MS 50U
#define DISPLAY_STATE_TRANSITION_MS 350U
#define DISPLAY_START_TIMEOUT_MS 5000U
#define DISPLAY_TASK_STACK_BYTES (12U * 1024U)
#define DISPLAY_TASK_PRIORITY 4U
#define FACE_COMMAND_QUEUE_LENGTH 8U

#define FACE_RENDER_X_START 12
#define FACE_RENDER_X_END 278
#define FACE_RENDER_Y_START 12
#define FACE_RENDER_Y_END 224
#define FACE_RENDER_WIDTH (FACE_RENDER_X_END - FACE_RENDER_X_START)
#define FACE_RENDER_HEIGHT (FACE_RENDER_Y_END - FACE_RENDER_Y_START)
#define FACE_LEFT_EYE_X 83
#define FACE_RIGHT_EYE_X 197
#define FACE_EYE_Y 147

static const char *TAG = "nana_display";

typedef uint8_t display_pixel_t;

enum {
    DISPLAY_COLOR_BACKGROUND = 0,
    DISPLAY_COLOR_EYE,
    DISPLAY_COLOR_LOADER_BRIGHT,
    DISPLAY_COLOR_LOADER_MID,
    DISPLAY_COLOR_LOADER_DIM,
    DISPLAY_COLOR_COUNT,
};

typedef struct {
    esp_lcd_panel_io_handle_t panel_io;
    esp_lcd_panel_handle_t panel;
    SemaphoreHandle_t transfer_done;
    display_pixel_t *framebuffer;
    uint16_t *transfer_buffer;
    uint16_t wire_palette[DISPLAY_COLOR_COUNT];
    bool bus_initialized;
} display_context_t;

typedef struct {
    display_pixel_t background;
    display_pixel_t eye;
    display_pixel_t loader_bright;
    display_pixel_t loader_mid;
    display_pixel_t loader_dim;
} face_palette_t;

typedef enum {
    EYE_STYLE_ROUNDED,
    EYE_STYLE_SLANTED,
    EYE_STYLE_ARC,
    EYE_STYLE_LINE,
    EYE_STYLE_HEART,
    EYE_STYLE_SPIRAL,
    EYE_STYLE_CHEVRON,
} eye_style_t;

typedef enum {
    FACE_MARK_NONE,
    FACE_MARK_TEAR,
    FACE_MARK_SWEAT,
    FACE_MARK_SPARKLES,
} face_mark_t;

typedef struct {
    nana_face_state_t state;
    uint32_t local_time_ms;
    uint32_t duration_ms;
    int eye_width[2];
    int eye_height[2];
    int eye_offset_x[2];
    int eye_offset_y[2];
    int eye_top_tilt[2];
    int eye_bottom_tilt[2];
    eye_style_t eye_style;
    int arc_depth;
    int arc_thickness;
    face_mark_t mark;
    int blink_per_mille[2];
    bool show_spinner;
    bool show_question;
    bool show_panic_mouth;
} face_scene_t;

typedef struct {
    nana_face_state_t state;
    const char *name;
    uint32_t duration_ms;
    nana_face_tag_kind_t kind;
} face_stage_t;

typedef enum {
    FACE_COMMAND_SET_EXPRESSION,
    FACE_COMMAND_TRIGGER_EVENT,
} face_command_kind_t;

typedef struct {
    face_command_kind_t kind;
    nana_face_state_t state;
} face_command_t;

static QueueHandle_t face_command_queue;
static TaskHandle_t face_controller_task;
static volatile bool face_controller_ready;
static volatile esp_err_t face_controller_start_result = ESP_ERR_INVALID_STATE;

#define EXPRESSION_STAGE(state, name, duration) \
    {state, name, duration, NANA_FACE_TAG_EXPRESSION}
#define GESTURE_STAGE(state, name, duration) \
    {state, name, duration, NANA_FACE_TAG_GESTURE}

static const face_stage_t FACE_STAGES[] = {
    EXPRESSION_STAGE(NANA_FACE_IDLE, "neutral", 4000U),
    EXPRESSION_STAGE(NANA_FACE_LISTENING, "listening", 3000U),
    EXPRESSION_STAGE(NANA_FACE_PONDERING, "pondering", 3000U),
    EXPRESSION_STAGE(NANA_FACE_HAPPY, "happy", 2500U),
    EXPRESSION_STAGE(NANA_FACE_LAUGHING, "laughing", 2500U),
    EXPRESSION_STAGE(NANA_FACE_GLEE, "glee", 2500U),
    EXPRESSION_STAGE(NANA_FACE_SLEEPING, "sleepy", 3000U),
    EXPRESSION_STAGE(NANA_FACE_SURPRISED, "surprised", 2500U),
    EXPRESSION_STAGE(NANA_FACE_SHOCKED, "shocked", 2500U),
    EXPRESSION_STAGE(NANA_FACE_SCARED, "scared", 2500U),
    EXPRESSION_STAGE(NANA_FACE_AWE, "awe", 2500U),
    EXPRESSION_STAGE(NANA_FACE_SKEPTICAL, "skeptical", 2500U),
    EXPRESSION_STAGE(NANA_FACE_SQUINT, "squint", 2500U),
    EXPRESSION_STAGE(NANA_FACE_UNCLEAR, "mic_unclear", 4000U),
    EXPRESSION_STAGE(NANA_FACE_THINKING, "core_loading", 5000U),
    EXPRESSION_STAGE(NANA_FACE_CURIOUS, "curious", 2600U),
    EXPRESSION_STAGE(NANA_FACE_PLAYFUL, "playful", 2400U),
    EXPRESSION_STAGE(NANA_FACE_SHY, "shy", 2400U),
    EXPRESSION_STAGE(NANA_FACE_RELIEVED, "relieved", 2400U),
    EXPRESSION_STAGE(NANA_FACE_PLEADING, "pleading", 2600U),
    EXPRESSION_STAGE(NANA_FACE_LOVE, "love", 2600U),
    EXPRESSION_STAGE(NANA_FACE_DIZZY, "dizzy", 2600U),
    EXPRESSION_STAGE(NANA_FACE_PROUD, "proud", 2400U),
    GESTURE_STAGE(NANA_FACE_WINK_LEFT, "wink_left", 1600U),
    GESTURE_STAGE(NANA_FACE_WINK_RIGHT, "wink_right", 1600U),
    GESTURE_STAGE(NANA_FACE_DOUBLE_BLINK, "double_blink", 1700U),
    GESTURE_STAGE(NANA_FACE_GLANCE_LEFT, "glance_left", 1600U),
    GESTURE_STAGE(NANA_FACE_GLANCE_RIGHT, "glance_right", 1600U),
    GESTURE_STAGE(NANA_FACE_GLANCE_UP, "glance_up", 1600U),
    GESTURE_STAGE(NANA_FACE_GLANCE_DOWN, "glance_down", 1600U),
    GESTURE_STAGE(NANA_FACE_LOOK_AROUND, "look_around", 2400U),
    GESTURE_STAGE(NANA_FACE_SCAN, "scan", 2400U),
    GESTURE_STAGE(NANA_FACE_PEEK_LEFT, "peek_left", 1800U),
    GESTURE_STAGE(NANA_FACE_PEEK_RIGHT, "peek_right", 1800U),
    GESTURE_STAGE(NANA_FACE_STARTLED_BLINK, "startled_blink", 1700U),
    GESTURE_STAGE(NANA_FACE_DOUBLE_TAKE, "double_take", 1900U),
    GESTURE_STAGE(NANA_FACE_CELEBRATE, "celebrate", 2100U),
    GESTURE_STAGE(NANA_FACE_PANIC, "panic", 2100U),
};

#undef EXPRESSION_STAGE
#undef GESTURE_STAGE

_Static_assert(sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]) ==
                   NANA_FACE_STATE_COUNT,
               "Every Nana face state must have exactly one tag row");

static const face_stage_t *face_stage_for_state(nana_face_state_t state)
{
    for (size_t i = 0; i < sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]); ++i) {
        if (FACE_STAGES[i].state == state) {
            return &FACE_STAGES[i];
        }
    }
    return NULL;
}

const char *nana_face_state_tag(nana_face_state_t state)
{
    for (size_t i = 0; i < sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]); ++i) {
        if (FACE_STAGES[i].state == state) {
            return FACE_STAGES[i].name;
        }
    }
    return "unknown";
}

bool nana_face_state_from_tag(const char *tag, nana_face_state_t *state)
{
    if (tag == NULL || state == NULL) {
        return false;
    }

    for (size_t i = 0; i < sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]); ++i) {
        if (strcmp(FACE_STAGES[i].name, tag) == 0) {
            *state = FACE_STAGES[i].state;
            return true;
        }
    }
    return false;
}

nana_face_tag_kind_t nana_face_state_tag_kind(nana_face_state_t state)
{
    for (size_t i = 0; i < sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]); ++i) {
        if (FACE_STAGES[i].state == state) {
            return FACE_STAGES[i].kind;
        }
    }
    return NANA_FACE_TAG_EXPRESSION;
}

bool nana_face_tag_is_gesture(const char *tag)
{
    nana_face_state_t state = NANA_FACE_IDLE;
    return nana_face_state_from_tag(tag, &state) &&
           nana_face_state_tag_kind(state) == NANA_FACE_TAG_GESTURE;
}

static bool queue_face_command(face_command_kind_t kind,
                               nana_face_state_t state)
{
    QueueHandle_t queue = face_command_queue;
    if (queue == NULL) {
        return false;
    }

    const face_command_t command = {
        .kind = kind,
        .state = state,
    };
    return xQueueSend(queue, &command, 0) == pdPASS;
}

bool nana_display_set_expression(const char *tag)
{
    nana_face_state_t state = NANA_FACE_IDLE;
    if (!nana_face_state_from_tag(tag, &state) ||
        nana_face_state_tag_kind(state) != NANA_FACE_TAG_EXPRESSION) {
        return false;
    }
    return queue_face_command(FACE_COMMAND_SET_EXPRESSION, state);
}

bool nana_display_trigger_event(const char *tag)
{
    nana_face_state_t state = NANA_FACE_IDLE;
    if (!nana_face_state_from_tag(tag, &state) || state == NANA_FACE_IDLE) {
        return false;
    }
    return queue_face_command(FACE_COMMAND_TRIGGER_EVENT, state);
}

bool nana_display_is_running(void)
{
    return face_controller_ready && face_command_queue != NULL;
}

static bool face_tag_contract_valid(void)
{
    bool state_seen[NANA_FACE_STATE_COUNT] = {false};
    for (size_t i = 0; i < sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]); ++i) {
        if ((unsigned)FACE_STAGES[i].state >=
                (unsigned)NANA_FACE_STATE_COUNT ||
            state_seen[FACE_STAGES[i].state]) {
            return false;
        }
        state_seen[FACE_STAGES[i].state] = true;

        nana_face_state_t parsed_state = NANA_FACE_IDLE;
        if (!nana_face_state_from_tag(FACE_STAGES[i].name, &parsed_state) ||
            parsed_state != FACE_STAGES[i].state ||
            strcmp(nana_face_state_tag(FACE_STAGES[i].state),
                   FACE_STAGES[i].name) != 0 ||
            nana_face_tag_is_gesture(FACE_STAGES[i].name) !=
                (FACE_STAGES[i].kind == NANA_FACE_TAG_GESTURE)) {
            return false;
        }

        for (size_t j = i + 1;
             j < sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]);
             ++j) {
            if (strcmp(FACE_STAGES[i].name, FACE_STAGES[j].name) == 0) {
                return false;
            }
        }
    }

    for (size_t i = 0; i < NANA_FACE_STATE_COUNT; ++i) {
        if (!state_seen[i]) {
            return false;
        }
    }

    nana_face_state_t ignored_state = NANA_FACE_IDLE;
    return !nana_face_state_from_tag("not_a_face_tag", &ignored_state);
}

static bool color_transfer_done(esp_lcd_panel_io_handle_t panel_io,
                                esp_lcd_panel_io_event_data_t *event_data,
                                void *user_context)
{
    (void)panel_io;
    (void)event_data;
    BaseType_t high_priority_task_woken = pdFALSE;
    xSemaphoreGiveFromISR((SemaphoreHandle_t)user_context,
                          &high_priority_task_woken);
    return high_priority_task_woken == pdTRUE;
}

static uint16_t rgb565_wire(uint8_t red, uint8_t green, uint8_t blue)
{
    const uint16_t rgb565 =
        ((uint16_t)(red & 0xF8U) << 8U) |
        ((uint16_t)(green & 0xFCU) << 3U) |
        ((uint16_t)blue >> 3U);
    return (uint16_t)((rgb565 << 8U) | (rgb565 >> 8U));
}

static face_palette_t face_palette(void)
{
    return (face_palette_t){
        .background = DISPLAY_COLOR_BACKGROUND,
        .eye = DISPLAY_COLOR_EYE,
        .loader_bright = DISPLAY_COLOR_LOADER_BRIGHT,
        .loader_mid = DISPLAY_COLOR_LOADER_MID,
        .loader_dim = DISPLAY_COLOR_LOADER_DIM,
    };
}

static int int_abs(int value)
{
    return value < 0 ? -value : value;
}

static int int_min(int left, int right)
{
    return left < right ? left : right;
}

static int int_max(int left, int right)
{
    return left > right ? left : right;
}

static void fill_span(display_context_t *context,
                      int y,
                      int x_start,
                      int x_end,
                      display_pixel_t color)
{
    if (y < FACE_RENDER_Y_START || y >= FACE_RENDER_Y_END) {
        return;
    }

    x_start = int_max(x_start, FACE_RENDER_X_START);
    x_end = int_min(x_end, FACE_RENDER_X_END);
    if (x_start >= x_end) {
        return;
    }

    display_pixel_t *row = context->framebuffer +
                           (y - FACE_RENDER_Y_START) * FACE_RENDER_WIDTH;
    for (int x = x_start; x < x_end; ++x) {
        row[x - FACE_RENDER_X_START] = color;
    }
}

static void fill_vertical_span(display_context_t *context,
                               int x,
                               int y_start,
                               int y_end,
                               display_pixel_t color)
{
    if (x < FACE_RENDER_X_START || x >= FACE_RENDER_X_END) {
        return;
    }

    y_start = int_max(y_start, FACE_RENDER_Y_START);
    y_end = int_min(y_end, FACE_RENDER_Y_END);
    for (int y = y_start; y < y_end; ++y) {
        display_pixel_t *row = context->framebuffer +
                               (y - FACE_RENDER_Y_START) * FACE_RENDER_WIDTH;
        row[x - FACE_RENDER_X_START] = color;
    }
}

static void fill_circle(display_context_t *context,
                        int center_x,
                        int center_y,
                        int radius,
                        display_pixel_t color)
{
    const int radius_squared = radius * radius;
    for (int dy = -radius; dy <= radius; ++dy) {
        const int span = (int)sqrtf((float)(radius_squared - dy * dy));
        fill_span(context,
                  center_y + dy,
                  center_x - span,
                  center_x + span + 1,
                  color);
    }
}

static void fill_rounded_rect(display_context_t *context,
                              int center_x,
                              int center_y,
                              int width,
                              int height,
                              int radius,
                              display_pixel_t color)
{
    if (width <= 0 || height <= 0) {
        return;
    }

    const int left = center_x - width / 2;
    const int top = center_y - height / 2;
    const int right = left + width;
    const int bottom = top + height;
    radius = int_min(radius, int_min(width, height) / 2);
    const int radius_squared = radius * radius;

    for (int y = top; y < bottom; ++y) {
        int inset = 0;
        if (radius > 0 && y < top + radius) {
            const int dy = top + radius - y;
            inset = radius -
                    (int)sqrtf((float)int_max(radius_squared - dy * dy, 0));
        } else if (radius > 0 && y >= bottom - radius) {
            const int dy = y - (bottom - radius - 1);
            inset = radius -
                    (int)sqrtf((float)int_max(radius_squared - dy * dy, 0));
        }
        fill_span(context, y, left + inset, right - inset, color);
    }
}

static void fill_slanted_eye(display_context_t *context,
                             int center_x,
                             int center_y,
                             int width,
                             int height,
                             int top_tilt,
                             int bottom_tilt,
                             display_pixel_t color)
{
    if (width <= 1 || height <= 1) {
        return;
    }

    const int left = center_x - width / 2;
    for (int column = 0; column < width; ++column) {
        const int top = center_y - height / 2 - top_tilt / 2 +
                        top_tilt * column / (width - 1);
        const int bottom = center_y + height / 2 - bottom_tilt / 2 +
                           bottom_tilt * column / (width - 1);
        const int edge_distance = int_min(column, width - 1 - column);
        const int edge_inset = int_max(3 - edge_distance, 0);
        fill_vertical_span(context,
                           left + column,
                           top + edge_inset,
                           bottom - edge_inset + 1,
                           color);
    }
}

static void draw_capsule(display_context_t *context,
                         int start_x,
                         int start_y,
                         int end_x,
                         int end_y,
                         int radius,
                         display_pixel_t color)
{
    const int delta_x = end_x - start_x;
    const int delta_y = end_y - start_y;
    const int steps = int_max(int_abs(delta_x), int_abs(delta_y));
    if (steps == 0) {
        fill_circle(context, start_x, start_y, radius, color);
        return;
    }

    for (int step = 0; step <= steps; ++step) {
        fill_circle(context,
                    start_x + delta_x * step / steps,
                    start_y + delta_y * step / steps,
                    radius,
                    color);
    }
}

static void draw_arc_eye(display_context_t *context,
                         int center_x,
                         int center_y,
                         int width,
                         int depth,
                         int thickness,
                         display_pixel_t color)
{
    const int half_width = int_max(width / 2, 1);
    const int denominator = half_width * half_width;
    for (int dx = -half_width; dx <= half_width; ++dx) {
        const int curve_y = center_y + dx * dx * depth / denominator;
        fill_circle(context,
                    center_x + dx,
                    curve_y,
                    int_max(thickness / 2, 2),
                    color);
    }
}

static void draw_heart_eye(display_context_t *context,
                           int center_x,
                           int center_y,
                           int width,
                           int height,
                           display_pixel_t color)
{
    width = int_max(width, 12);
    height = int_max(height, 12);
    const int lobe_radius = int_max(int_min(width / 4, height / 4), 3);
    const int lobe_y = center_y - height / 4;
    fill_circle(context,
                center_x - lobe_radius,
                lobe_y,
                lobe_radius,
                color);
    fill_circle(context,
                center_x + lobe_radius,
                lobe_y,
                lobe_radius,
                color);

    const int body_top = lobe_y;
    const int body_bottom = center_y + height / 2;
    const int body_height = int_max(body_bottom - body_top, 1);
    for (int y = body_top; y <= body_bottom; ++y) {
        const int half_width =
            (width / 2) * (body_bottom - y) / body_height;
        fill_span(context,
                  y,
                  center_x - half_width,
                  center_x + half_width + 1,
                  color);
    }
}

static void draw_spiral_eye(display_context_t *context,
                            int center_x,
                            int center_y,
                            int width,
                            int height,
                            float phase,
                            int direction,
                            display_pixel_t color)
{
    const int maximum_radius = int_max(int_min(width, height) / 2 - 2, 5);
    const int segment_count = 40;
    int previous_x = center_x;
    int previous_y = center_y;
    for (int segment = 1; segment <= segment_count; ++segment) {
        const float progress = (float)segment / (float)segment_count;
        const float radius = 2.0f +
                             (float)(maximum_radius - 2) * progress;
        const float angle = phase +
                            (float)direction * progress * 14.2f;
        const int x = center_x + (int)(cosf(angle) * radius);
        const int y = center_y + (int)(sinf(angle) * radius);
        draw_capsule(context,
                     previous_x,
                     previous_y,
                     x,
                     y,
                     2,
                     color);
        previous_x = x;
        previous_y = y;
    }
}

static void draw_chevron_eye(display_context_t *context,
                             int center_x,
                             int center_y,
                             int width,
                             int height,
                             bool points_right,
                             display_pixel_t color)
{
    const int outer_x = center_x + (points_right ? -width / 2 : width / 2);
    const int inner_x = center_x + (points_right ? width / 3 : -width / 3);
    const int radius = int_max(height / 11, 3);
    draw_capsule(context,
                 outer_x,
                 center_y - height / 2,
                 inner_x,
                 center_y,
                 radius,
                 color);
    draw_capsule(context,
                 inner_x,
                 center_y,
                 outer_x,
                 center_y + height / 2,
                 radius,
                 color);
}

static size_t face_tag_kind_count(nana_face_tag_kind_t kind)
{
    size_t count = 0;
    for (size_t i = 0; i < sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]); ++i) {
        if (FACE_STAGES[i].kind == kind) {
            ++count;
        }
    }
    return count;
}

static int transition_blink_per_mille(uint32_t local_time_ms,
                                      uint32_t duration_ms)
{
    if (local_time_ms < DISPLAY_STATE_TRANSITION_MS) {
        return 1000 - (int)(local_time_ms * 1000U /
                            DISPLAY_STATE_TRANSITION_MS);
    }

    const uint32_t remaining_ms = duration_ms - local_time_ms;
    if (remaining_ms < DISPLAY_STATE_TRANSITION_MS) {
        return 1000 - (int)(remaining_ms * 1000U /
                            DISPLAY_STATE_TRANSITION_MS);
    }
    return 0;
}

static int natural_blink_per_mille(uint32_t local_time_ms)
{
    static const uint16_t intervals_ms[] = {
        4200U, 6100U, 4900U, 7300U, 5400U,
    };
    const uint32_t blink_duration_ms = 240U;
    uint32_t sequence_duration_ms = 0;
    for (size_t i = 0;
         i < sizeof(intervals_ms) / sizeof(intervals_ms[0]);
         ++i) {
        sequence_duration_ms += intervals_ms[i];
    }

    const uint32_t sequence_time_ms =
        local_time_ms % sequence_duration_ms;
    uint32_t interval_end_ms = 0;
    for (size_t i = 0;
         i < sizeof(intervals_ms) / sizeof(intervals_ms[0]);
         ++i) {
        interval_end_ms += intervals_ms[i];
        const uint32_t blink_start_ms =
            interval_end_ms - blink_duration_ms;
        if (sequence_time_ms < blink_start_ms ||
            sequence_time_ms >= interval_end_ms) {
            continue;
        }

        const uint32_t blink_time_ms =
            sequence_time_ms - blink_start_ms;
        if (blink_time_ms < blink_duration_ms / 2U) {
            return (int)(blink_time_ms * 2000U / blink_duration_ms);
        }
        return (int)((blink_duration_ms - blink_time_ms) * 2000U /
                     blink_duration_ms);
    }
    return 0;
}

static int smooth_step_per_mille(uint32_t elapsed_ms, uint32_t duration_ms)
{
    if (duration_ms == 0U || elapsed_ms >= duration_ms) {
        return 1000;
    }
    const float progress = (float)elapsed_ms / (float)duration_ms;
    const float eased = progress * progress * (3.0f - 2.0f * progress);
    return (int)(eased * 1000.0f);
}

static int gesture_envelope_per_mille(uint32_t local_time_ms,
                                      uint32_t start_ms,
                                      uint32_t rise_ms,
                                      uint32_t hold_ms,
                                      uint32_t fall_ms)
{
    if (local_time_ms < start_ms) {
        return 0;
    }

    uint32_t elapsed_ms = local_time_ms - start_ms;
    if (elapsed_ms < rise_ms) {
        return smooth_step_per_mille(elapsed_ms, rise_ms);
    }
    elapsed_ms -= rise_ms;
    if (elapsed_ms < hold_ms) {
        return 1000;
    }
    elapsed_ms -= hold_ms;
    if (elapsed_ms < fall_ms) {
        return 1000 - smooth_step_per_mille(elapsed_ms, fall_ms);
    }
    return 0;
}

static void apply_blink_both(face_scene_t *scene, int blink_per_mille)
{
    scene->blink_per_mille[0] =
        int_max(scene->blink_per_mille[0], blink_per_mille);
    scene->blink_per_mille[1] =
        int_max(scene->blink_per_mille[1], blink_per_mille);
}

static void offset_both_eyes(face_scene_t *scene, int x, int y)
{
    scene->eye_offset_x[0] += x;
    scene->eye_offset_x[1] += x;
    scene->eye_offset_y[0] += y;
    scene->eye_offset_y[1] += y;
}

static face_scene_t build_face_scene(const face_stage_t *stage,
                                     uint32_t local_time_ms)
{
    const int transition_blink = transition_blink_per_mille(
        local_time_ms, stage->duration_ms);
    face_scene_t scene = {
        .state = stage->state,
        .local_time_ms = local_time_ms,
        .duration_ms = stage->duration_ms,
        .eye_width = {82, 82},
        .eye_height = {72, 72},
        .eye_offset_x = {0, 0},
        .eye_offset_y = {0, 0},
        .eye_top_tilt = {0, 0},
        .eye_bottom_tilt = {0, 0},
        .eye_style = EYE_STYLE_ROUNDED,
        .arc_depth = 10,
        .arc_thickness = 8,
        .mark = FACE_MARK_NONE,
        .blink_per_mille = {transition_blink, transition_blink},
        .show_spinner = false,
        .show_question = false,
        .show_panic_mouth = false,
    };

    const float seconds = (float)local_time_ms / 1000.0f;
    switch (stage->state) {
    case NANA_FACE_IDLE:
        scene.eye_height[0] += (int)(sinf(seconds * 1.4f) * 2.0f);
        scene.eye_height[1] = scene.eye_height[0];
        apply_blink_both(&scene, natural_blink_per_mille(local_time_ms));
        break;
    case NANA_FACE_LISTENING:
        scene.eye_width[0] = 70;
        scene.eye_width[1] = 70;
        scene.eye_height[0] = 58 + (int)(sinf(seconds * 4.2f) * 2.0f);
        scene.eye_height[1] = scene.eye_height[0];
        apply_blink_both(&scene, natural_blink_per_mille(local_time_ms));
        break;
    case NANA_FACE_PONDERING:
        scene.eye_width[0] = 76;
        scene.eye_width[1] = 76;
        scene.eye_height[0] = 42;
        scene.eye_height[1] = 42;
        scene.eye_offset_y[0] = -3;
        scene.eye_offset_y[1] = -3;
        break;
    case NANA_FACE_HAPPY:
        scene.eye_style = EYE_STYLE_ARC;
        scene.eye_width[0] = 72;
        scene.eye_width[1] = 72;
        break;
    case NANA_FACE_LAUGHING:
        scene.eye_style = EYE_STYLE_ARC;
        scene.eye_width[0] = 76;
        scene.eye_width[1] = 76;
        scene.arc_depth = 15;
        scene.arc_thickness = 12;
        scene.eye_offset_y[0] = (int)(sinf(seconds * 9.0f) * 2.0f);
        scene.eye_offset_y[1] = scene.eye_offset_y[0];
        break;
    case NANA_FACE_GLEE:
        scene.eye_style = EYE_STYLE_ARC;
        scene.eye_width[0] = 84;
        scene.eye_width[1] = 84;
        scene.arc_depth = 20;
        scene.arc_thickness = 10;
        break;
    case NANA_FACE_SLEEPING:
        scene.eye_style = EYE_STYLE_LINE;
        scene.eye_width[0] = 78;
        scene.eye_width[1] = 78;
        scene.eye_offset_y[0] = 9;
        scene.eye_offset_y[1] = 9;
        break;
    case NANA_FACE_SURPRISED:
        scene.eye_width[0] = 90;
        scene.eye_width[1] = 90;
        scene.eye_height[0] = 90;
        scene.eye_height[1] = 90;
        break;
    case NANA_FACE_SHOCKED:
        scene.eye_width[0] = 94;
        scene.eye_width[1] = 94;
        scene.eye_height[0] = 102 + (int)(sinf(seconds * 5.0f) * 2.0f);
        scene.eye_height[1] = scene.eye_height[0];
        break;
    case NANA_FACE_SCARED:
        scene.eye_width[0] = 74;
        scene.eye_width[1] = 68;
        scene.eye_height[0] = 86;
        scene.eye_height[1] = 76;
        scene.eye_offset_x[0] = -5;
        scene.eye_offset_x[1] = 4;
        scene.eye_offset_y[1] = 4;
        break;
    case NANA_FACE_AWE:
        scene.eye_width[0] = 94;
        scene.eye_width[1] = 94;
        scene.eye_height[0] = 106;
        scene.eye_height[1] = 106;
        break;
    case NANA_FACE_SKEPTICAL:
        scene.eye_width[0] = 66;
        scene.eye_width[1] = 52;
        scene.eye_height[0] = 64;
        scene.eye_height[1] = 30;
        scene.eye_offset_x[0] = -7;
        scene.eye_offset_x[1] = -4;
        scene.eye_offset_y[1] = 5;
        break;
    case NANA_FACE_SQUINT:
        scene.eye_width[0] = 68;
        scene.eye_width[1] = 68;
        scene.eye_height[0] = 12;
        scene.eye_height[1] = 12;
        scene.eye_offset_y[0] = 5;
        scene.eye_offset_y[1] = 5;
        break;
    case NANA_FACE_UNCLEAR:
        scene.eye_width[0] = 80;
        scene.eye_width[1] = 80;
        scene.eye_height[0] = 30;
        scene.eye_height[1] = 30;
        scene.eye_offset_y[0] = 3;
        scene.eye_offset_y[1] = 3;
        scene.show_question = true;
        break;
    case NANA_FACE_THINKING:
        scene.eye_width[0] = 80;
        scene.eye_width[1] = 80;
        scene.eye_height[0] = 58;
        scene.eye_height[1] = 58;
        scene.eye_offset_y[0] = 5;
        scene.eye_offset_y[1] = 5;
        scene.show_spinner = true;
        apply_blink_both(&scene, natural_blink_per_mille(local_time_ms));
        break;
    case NANA_FACE_CURIOUS: {
        const int look_x = (int)(sinf(seconds * 1.7f) * 15.0f);
        scene.eye_width[0] = 72;
        scene.eye_width[1] = 72;
        scene.eye_height[0] = look_x < -3 ? 88 : 64;
        scene.eye_height[1] = look_x > 3 ? 88 : 64;
        offset_both_eyes(&scene, look_x, -2);
        break;
    }
    case NANA_FACE_PLAYFUL: {
        const uint32_t wink_time_ms = local_time_ms % 3200U;
        scene.eye_width[0] = 88;
        scene.eye_width[1] = 70;
        scene.eye_height[0] = 70;
        scene.eye_height[1] = 60;
        scene.eye_offset_y[0] = (int)(sinf(seconds * 3.0f) * 2.0f) - 2;
        scene.eye_offset_y[1] = 4;
        scene.blink_per_mille[1] = int_max(
            scene.blink_per_mille[1],
            gesture_envelope_per_mille(
                wink_time_ms, 1450U, 100U, 130U, 180U));
        break;
    }
    case NANA_FACE_SHY:
        scene.eye_style = EYE_STYLE_ARC;
        scene.eye_width[0] = 62;
        scene.eye_width[1] = 62;
        scene.arc_depth = 12;
        scene.arc_thickness = 8;
        scene.eye_offset_x[0] = 5;
        scene.eye_offset_x[1] = -5;
        scene.eye_offset_y[0] = 8;
        scene.eye_offset_y[1] = 8;
        break;
    case NANA_FACE_RELIEVED:
        scene.eye_style = EYE_STYLE_ARC;
        scene.eye_width[0] = 80;
        scene.eye_width[1] = 80;
        scene.arc_depth = 7;
        scene.arc_thickness = 7;
        scene.eye_offset_y[0] = 5;
        scene.eye_offset_y[1] = 5;
        break;
    case NANA_FACE_PLEADING:
        scene.eye_width[0] = 64;
        scene.eye_width[1] = 64;
        scene.eye_height[0] = 96 + (int)(sinf(seconds * 2.2f) * 3.0f);
        scene.eye_height[1] = scene.eye_height[0];
        scene.eye_offset_x[0] = 5;
        scene.eye_offset_x[1] = -5;
        scene.eye_offset_y[0] = 5;
        scene.eye_offset_y[1] = 5;
        break;
    case NANA_FACE_LOVE: {
        const int pulse = (int)((sinf(seconds * 4.2f) + 1.0f) * 3.0f);
        scene.eye_style = EYE_STYLE_HEART;
        scene.eye_width[0] = 68 + pulse;
        scene.eye_width[1] = 68 + pulse;
        scene.eye_height[0] = 72 + pulse;
        scene.eye_height[1] = 72 + pulse;
        break;
    }
    case NANA_FACE_DIZZY:
        scene.eye_style = EYE_STYLE_SPIRAL;
        scene.eye_width[0] = 72;
        scene.eye_width[1] = 72;
        scene.eye_height[0] = 72;
        scene.eye_height[1] = 72;
        break;
    case NANA_FACE_PROUD:
        scene.eye_style = EYE_STYLE_ARC;
        scene.eye_width[0] = 82;
        scene.eye_width[1] = 72;
        scene.arc_depth = 13;
        scene.arc_thickness = 9;
        scene.eye_offset_y[0] = -4;
        scene.eye_offset_y[1] = 1;
        break;
    case NANA_FACE_WINK_LEFT:
        scene.blink_per_mille[0] = int_max(
            scene.blink_per_mille[0],
            gesture_envelope_per_mille(
                local_time_ms, 470U, 100U, 180U, 210U));
        scene.eye_width[1] = 86;
        scene.eye_height[1] = 76;
        break;
    case NANA_FACE_WINK_RIGHT:
        scene.blink_per_mille[1] = int_max(
            scene.blink_per_mille[1],
            gesture_envelope_per_mille(
                local_time_ms, 470U, 100U, 180U, 210U));
        scene.eye_width[0] = 86;
        scene.eye_height[0] = 76;
        break;
    case NANA_FACE_DOUBLE_BLINK:
        apply_blink_both(
            &scene,
            int_max(gesture_envelope_per_mille(
                        local_time_ms, 390U, 90U, 70U, 140U),
                    gesture_envelope_per_mille(
                        local_time_ms, 870U, 90U, 70U, 150U)));
        break;
    case NANA_FACE_GLANCE_LEFT:
    case NANA_FACE_GLANCE_RIGHT:
    case NANA_FACE_GLANCE_UP:
    case NANA_FACE_GLANCE_DOWN: {
        const int envelope = gesture_envelope_per_mille(
            local_time_ms, 360U, 260U, 500U, 280U);
        const int horizontal =
            stage->state == NANA_FACE_GLANCE_LEFT
                ? -22
                : (stage->state == NANA_FACE_GLANCE_RIGHT ? 22 : 0);
        const int vertical =
            stage->state == NANA_FACE_GLANCE_UP
                ? -18
                : (stage->state == NANA_FACE_GLANCE_DOWN ? 18 : 0);
        offset_both_eyes(&scene,
                         horizontal * envelope / 1000,
                         vertical * envelope / 1000);
        break;
    }
    case NANA_FACE_LOOK_AROUND: {
        const float progress =
            (float)local_time_ms / (float)stage->duration_ms;
        offset_both_eyes(&scene,
                         (int)(sinf(progress * 6.2831853f) * 20.0f),
                         (int)(sinf(progress * 12.5663706f) * 9.0f));
        break;
    }
    case NANA_FACE_SCAN: {
        const float progress =
            (float)local_time_ms / (float)stage->duration_ms;
        offset_both_eyes(&scene,
                         (int)(sinf(progress * 6.2831853f) * 26.0f),
                         0);
        scene.eye_height[0] = 46;
        scene.eye_height[1] = 46;
        break;
    }
    case NANA_FACE_PEEK_LEFT:
    case NANA_FACE_PEEK_RIGHT: {
        const int envelope = gesture_envelope_per_mille(
            local_time_ms, 350U, 260U, 650U, 300U);
        const bool left = stage->state == NANA_FACE_PEEK_LEFT;
        const int direction = left ? -1 : 1;
        offset_both_eyes(&scene, direction * 25 * envelope / 1000, 0);
        scene.eye_height[left ? 0 : 1] += 22 * envelope / 1000;
        scene.eye_height[left ? 1 : 0] -= 10 * envelope / 1000;
        break;
    }
    case NANA_FACE_STARTLED_BLINK: {
        const int surprise = gesture_envelope_per_mille(
            local_time_ms, 300U, 160U, 350U, 220U);
        scene.eye_width[0] += 12 * surprise / 1000;
        scene.eye_width[1] += 12 * surprise / 1000;
        scene.eye_height[0] += 24 * surprise / 1000;
        scene.eye_height[1] += 24 * surprise / 1000;
        apply_blink_both(&scene,
                         gesture_envelope_per_mille(
                             local_time_ms, 1020U, 80U, 60U, 140U));
        break;
    }
    case NANA_FACE_DOUBLE_TAKE: {
        const int right = gesture_envelope_per_mille(
            local_time_ms, 260U, 150U, 240U, 140U);
        const int left = gesture_envelope_per_mille(
            local_time_ms, 820U, 140U, 300U, 180U);
        offset_both_eyes(&scene, 22 * (right - left) / 1000, 0);
        apply_blink_both(&scene,
                         gesture_envelope_per_mille(
                             local_time_ms, 700U, 70U, 40U, 110U));
        break;
    }
    case NANA_FACE_CELEBRATE:
        scene.eye_style = EYE_STYLE_ARC;
        scene.eye_width[0] = 80;
        scene.eye_width[1] = 80;
        scene.arc_depth = 15;
        scene.arc_thickness = 10;
        scene.eye_offset_y[0] = -(int)(fabsf(sinf(seconds * 8.0f)) * 10.0f);
        scene.eye_offset_y[1] = scene.eye_offset_y[0];
        scene.mark = FACE_MARK_SPARKLES;
        break;
    case NANA_FACE_PANIC: {
        const int shake = (int)(sinf(seconds * 24.0f) * 4.0f);
        scene.eye_style = EYE_STYLE_CHEVRON;
        scene.eye_width[0] = 74;
        scene.eye_width[1] = 74;
        scene.eye_height[0] = 46;
        scene.eye_height[1] = 46;
        scene.eye_offset_x[0] = shake;
        scene.eye_offset_x[1] = shake;
        scene.show_panic_mouth = local_time_ms >= 300U &&
                                 local_time_ms + 300U < stage->duration_ms;
        break;
    }
    default:
        break;
    }

    if (stage->kind == NANA_FACE_TAG_EXPRESSION &&
        stage->state != NANA_FACE_SLEEPING) {
        apply_blink_both(&scene, natural_blink_per_mille(local_time_ms));
    }

    if (stage->state != NANA_FACE_SLEEPING) {
        const int breathe = (int)(sinf(seconds * 1.8f) * 1.4f);
        scene.eye_offset_y[0] += breathe;
        scene.eye_offset_y[1] += breathe;
    }
    return scene;
}

static void draw_spinner(display_context_t *context,
                         const face_scene_t *scene,
                         const face_palette_t *palette)
{
    static const int8_t offsets_x[] = {0, 13, 18, 13, 0, -13, -18, -13};
    static const int8_t offsets_y[] = {-18, -13, 0, 13, 18, 13, 0, -13};
    if (!scene->show_spinner) {
        return;
    }

    const int center_x = 140;
    const int center_y = 49;
    const size_t dot_count = sizeof(offsets_x) / sizeof(offsets_x[0]);
    const size_t phase = (scene->local_time_ms / 110U) % dot_count;
    for (size_t i = 0; i < dot_count; ++i) {
        const size_t age = (phase + dot_count - i) % dot_count;
        const display_pixel_t color =
            age == 0 ? palette->loader_bright
                     : (age <= 2 ? palette->loader_mid
                                 : palette->loader_dim);
        fill_circle(context,
                    center_x + offsets_x[i],
                    center_y + offsets_y[i],
                    4,
                    color);
    }
}

static void draw_question_mark(display_context_t *context,
                               const face_scene_t *scene,
                               const face_palette_t *palette)
{
    if (!scene->show_question) {
        return;
    }

    const int bob = (int)(sinf((float)scene->local_time_ms / 260.0f) * 2.0f);
    const int x = 248;
    const int y = 101 + bob;
    draw_capsule(context, x - 8, y - 9, x - 4, y - 13, 2,
                 palette->loader_bright);
    draw_capsule(context, x - 4, y - 13, x + 2, y - 13, 2,
                 palette->loader_bright);
    draw_capsule(context, x + 2, y - 13, x + 6, y - 8, 2,
                 palette->loader_bright);
    draw_capsule(context, x + 6, y - 8, x + 6, y - 3, 2,
                 palette->loader_bright);
    draw_capsule(context, x + 6, y - 3, x, y + 2, 2,
                 palette->loader_bright);
    draw_capsule(context, x, y + 2, x, y + 6, 2,
                 palette->loader_bright);
    fill_circle(context, x, y + 13, 3, palette->loader_bright);
}

static void draw_face_mark(display_context_t *context,
                           const face_scene_t *scene,
                           const face_palette_t *palette)
{
    const int bob = (int)(sinf((float)scene->local_time_ms / 210.0f) * 2.0f);
    if (scene->mark == FACE_MARK_TEAR) {
        draw_capsule(context, 247, 160 + bob, 247, 174 + bob, 3,
                     palette->loader_bright);
        fill_circle(context, 247, 177 + bob, 5, palette->loader_bright);
    } else if (scene->mark == FACE_MARK_SWEAT) {
        draw_capsule(context, 246, 100 + bob, 242, 111 + bob, 3,
                     palette->loader_bright);
        fill_circle(context, 241, 114 + bob, 4, palette->loader_bright);
    } else if (scene->mark == FACE_MARK_SPARKLES) {
        const int pulse = 7 +
                          (int)((sinf((float)scene->local_time_ms / 150.0f) +
                                 1.0f) *
                                2.0f);
        draw_capsule(context, 32 - pulse, 96, 32 + pulse, 96, 2,
                     palette->loader_bright);
        draw_capsule(context, 32, 96 - pulse, 32, 96 + pulse, 2,
                     palette->loader_bright);
        draw_capsule(context, 249 - pulse / 2, 109,
                     249 + pulse / 2, 109, 2, palette->loader_mid);
        draw_capsule(context, 249, 109 - pulse / 2,
                     249, 109 + pulse / 2, 2, palette->loader_mid);
    }
}

static void draw_panic_mouth(display_context_t *context,
                             const face_scene_t *scene,
                             const face_palette_t *palette)
{
    if (!scene->show_panic_mouth) {
        return;
    }

    const float seconds = (float)scene->local_time_ms / 1000.0f;
    const int shake = (int)(sinf(seconds * 24.0f) * 3.0f);
    const int bob = (int)(sinf(seconds * 10.0f) * 2.0f);
    const int center_x = 140 + shake;
    const int center_y = 202 + bob;
    draw_capsule(context, center_x - 31, center_y - 2,
                 center_x - 17, center_y - 7, 4, palette->eye);
    draw_capsule(context, center_x - 17, center_y - 7,
                 center_x, center_y + 2, 4, palette->eye);
    draw_capsule(context, center_x, center_y + 2,
                 center_x + 17, center_y - 7, 4, palette->eye);
    draw_capsule(context, center_x + 17, center_y - 7,
                 center_x + 31, center_y - 2, 4, palette->eye);
}

static void draw_eye(display_context_t *context,
                     const face_scene_t *scene,
                     const face_palette_t *palette,
                     size_t eye_index)
{
    static const int eye_centers[] = {FACE_LEFT_EYE_X, FACE_RIGHT_EYE_X};
    const int center_x = eye_centers[eye_index] + scene->eye_offset_x[eye_index];
    const int center_y = FACE_EYE_Y + scene->eye_offset_y[eye_index];
    const int width = scene->eye_width[eye_index];
    const int openness = 1000 - scene->blink_per_mille[eye_index];

    if (scene->eye_style == EYE_STYLE_ARC) {
        const int depth = scene->arc_depth * openness / 1000;
        const int thickness = 6 +
                              int_max(scene->arc_thickness - 6, 0) *
                                  openness / 1000;
        draw_arc_eye(context,
                     center_x,
                     center_y,
                     width,
                     depth,
                     thickness,
                     palette->eye);
        return;
    }
    if (scene->eye_style == EYE_STYLE_LINE) {
        draw_capsule(context,
                     center_x - width / 2,
                     center_y,
                     center_x + width / 2,
                     center_y,
                     4,
                     palette->eye);
        return;
    }

    int visible_height = scene->eye_height[eye_index] * openness / 1000;
    visible_height = int_max(visible_height, 5);
    if (visible_height <= 10) {
        draw_capsule(context,
                     center_x - width / 3,
                     center_y,
                     center_x + width / 3,
                     center_y,
                     3,
                     palette->eye);
        return;
    }

    if (scene->eye_style == EYE_STYLE_HEART) {
        draw_heart_eye(context,
                       center_x,
                       center_y,
                       width,
                       visible_height,
                       palette->eye);
    } else if (scene->eye_style == EYE_STYLE_SPIRAL) {
        const float phase =
            (float)scene->local_time_ms / 230.0f;
        draw_spiral_eye(context,
                        center_x,
                        center_y,
                        width,
                        visible_height,
                        phase,
                        eye_index == 0 ? 1 : -1,
                        palette->eye);
    } else if (scene->eye_style == EYE_STYLE_CHEVRON) {
        draw_chevron_eye(context,
                         center_x,
                         center_y,
                         width,
                         visible_height,
                         eye_index == 0,
                         palette->eye);
    } else if (scene->eye_style == EYE_STYLE_SLANTED) {
        const int source_height = int_max(scene->eye_height[eye_index], 1);
        fill_slanted_eye(
            context,
            center_x,
            center_y,
            width,
            visible_height,
            scene->eye_top_tilt[eye_index] * visible_height / source_height,
            scene->eye_bottom_tilt[eye_index] * visible_height / source_height,
            palette->eye);
    } else {
        fill_rounded_rect(context,
                          center_x,
                          center_y,
                          width,
                          visible_height,
                          int_min(22, visible_height / 2),
                          palette->eye);
    }
}

static void render_face(display_context_t *context,
                        const face_scene_t *scene,
                        const face_palette_t *palette)
{
    const size_t pixel_count = FACE_RENDER_WIDTH * FACE_RENDER_HEIGHT;
    for (size_t i = 0; i < pixel_count; ++i) {
        context->framebuffer[i] = palette->background;
    }

    draw_spinner(context, scene, palette);
    draw_question_mark(context, scene, palette);
    draw_face_mark(context, scene, palette);

    for (size_t i = 0; i < 2; ++i) {
        draw_eye(context, scene, palette, i);
    }
    draw_panic_mouth(context, scene, palette);
}

static esp_err_t draw_region(display_context_t *context,
                             const uint16_t *pixels,
                             int x_start,
                             int y_start,
                             int x_end,
                             int y_end)
{
    esp_err_t result = esp_lcd_panel_draw_bitmap(
        context->panel, x_start, y_start, x_end, y_end, pixels);
    if (result != ESP_OK) {
        return result;
    }
    return xSemaphoreTake(context->transfer_done, pdMS_TO_TICKS(1000)) == pdTRUE
               ? ESP_OK
               : ESP_ERR_TIMEOUT;
}

static esp_err_t clear_display(display_context_t *context,
                               display_pixel_t color)
{
    const size_t stripe_pixels = DISPLAY_WIDTH * DISPLAY_STRIPE_HEIGHT;
    const uint16_t wire_color =
        context->wire_palette[color < DISPLAY_COLOR_COUNT
                                  ? color
                                  : DISPLAY_COLOR_BACKGROUND];
    for (size_t i = 0; i < stripe_pixels; ++i) {
        context->transfer_buffer[i] = wire_color;
    }

    for (int y = 0; y < DISPLAY_HEIGHT; y += DISPLAY_STRIPE_HEIGHT) {
        const int y_end = int_min(y + DISPLAY_STRIPE_HEIGHT, DISPLAY_HEIGHT);
        const esp_err_t result = draw_region(
            context, context->transfer_buffer, 0, y, DISPLAY_WIDTH, y_end);
        if (result != ESP_OK) {
            return result;
        }
    }
    return ESP_OK;
}

static esp_err_t transfer_face(display_context_t *context)
{
    for (int y_start = FACE_RENDER_Y_START;
         y_start < FACE_RENDER_Y_END;
         y_start += DISPLAY_STRIPE_HEIGHT) {
        const int y_end = int_min(
            y_start + DISPLAY_STRIPE_HEIGHT, FACE_RENDER_Y_END);
        const display_pixel_t *stripe =
            context->framebuffer +
            (y_start - FACE_RENDER_Y_START) * FACE_RENDER_WIDTH;
        const size_t stripe_pixels =
            (size_t)FACE_RENDER_WIDTH * (size_t)(y_end - y_start);
        for (size_t i = 0; i < stripe_pixels; ++i) {
            const display_pixel_t color = stripe[i];
            context->transfer_buffer[i] =
                context->wire_palette[color < DISPLAY_COLOR_COUNT
                                          ? color
                                          : DISPLAY_COLOR_BACKGROUND];
        }
        const esp_err_t result = draw_region(context,
                                             context->transfer_buffer,
                                             FACE_RENDER_X_START,
                                             y_start,
                                             FACE_RENDER_X_END,
                                             y_end);
        if (result != ESP_OK) {
            return result;
        }
    }
    return ESP_OK;
}

static void display_close(display_context_t *context)
{
    if (context->panel != NULL) {
        esp_lcd_panel_disp_on_off(context->panel, false);
        esp_lcd_panel_del(context->panel);
    }
    if (context->panel_io != NULL) {
        esp_lcd_panel_io_del(context->panel_io);
    }
    if (context->bus_initialized) {
        spi_bus_free(DISPLAY_HOST);
    }
    free(context->transfer_buffer);
    free(context->framebuffer);
    if (context->transfer_done != NULL) {
        vSemaphoreDelete(context->transfer_done);
    }
}

static esp_err_t display_open(display_context_t *context)
{
    const size_t framebuffer_bytes =
        FACE_RENDER_WIDTH * FACE_RENDER_HEIGHT * sizeof(display_pixel_t);
    const size_t transfer_buffer_bytes =
        DISPLAY_WIDTH * DISPLAY_STRIPE_HEIGHT * sizeof(uint16_t);

    ESP_LOGI(TAG,
             "ST7789 wiring: VCC=3V3 GND=GND SCL=GPIO%d SDA=GPIO%d "
             "RES=GPIO%d DC=GPIO%d CS=GPIO%d BLK=3V3",
             NANA_DISPLAY_SCLK_GPIO,
             NANA_DISPLAY_MOSI_GPIO,
             NANA_DISPLAY_RESET_GPIO,
             NANA_DISPLAY_DC_GPIO,
             NANA_DISPLAY_CS_GPIO);
    ESP_LOGW(TAG, "Display uses the SD pins; microSD must remain removed");

    context->transfer_done = xSemaphoreCreateBinary();
    if (context->transfer_done == NULL) {
        return ESP_ERR_NO_MEM;
    }

    context->framebuffer = heap_caps_malloc(
        framebuffer_bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT);
    if (context->framebuffer == NULL) {
        return ESP_ERR_NO_MEM;
    }
    context->transfer_buffer = heap_caps_malloc(
        transfer_buffer_bytes, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (context->transfer_buffer == NULL) {
        return ESP_ERR_NO_MEM;
    }

    context->wire_palette[DISPLAY_COLOR_BACKGROUND] =
        rgb565_wire(0, 0, 0);
    context->wire_palette[DISPLAY_COLOR_EYE] =
        rgb565_wire(35, 205, 255);
    context->wire_palette[DISPLAY_COLOR_LOADER_BRIGHT] =
        rgb565_wire(72, 238, 255);
    context->wire_palette[DISPLAY_COLOR_LOADER_MID] =
        rgb565_wire(24, 143, 190);
    context->wire_palette[DISPLAY_COLOR_LOADER_DIM] =
        rgb565_wire(5, 45, 72);
    ESP_LOGI(TAG,
             "Display buffers: indexed=%zu bytes | dma_stripe=%zu bytes | "
             "internal_free=%u",
             framebuffer_bytes,
             transfer_buffer_bytes,
             (unsigned)heap_caps_get_free_size(MALLOC_CAP_INTERNAL));

    const spi_bus_config_t bus_config = {
        .sclk_io_num = NANA_DISPLAY_SCLK_GPIO,
        .mosi_io_num = NANA_DISPLAY_MOSI_GPIO,
        .miso_io_num = GPIO_NUM_NC,
        .quadwp_io_num = GPIO_NUM_NC,
        .quadhd_io_num = GPIO_NUM_NC,
        .max_transfer_sz = transfer_buffer_bytes,
    };
    esp_err_t result =
        spi_bus_initialize(DISPLAY_HOST, &bus_config, SPI_DMA_CH_AUTO);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "SPI bus init failed: %s", esp_err_to_name(result));
        return result;
    }
    context->bus_initialized = true;

    const esp_lcd_panel_io_spi_config_t io_config = {
        .cs_gpio_num = NANA_DISPLAY_CS_GPIO,
        .dc_gpio_num = NANA_DISPLAY_DC_GPIO,
        .spi_mode = 0,
        .pclk_hz = DISPLAY_PIXEL_CLOCK_HZ,
        .trans_queue_depth = 1,
        .on_color_trans_done = color_transfer_done,
        .user_ctx = context->transfer_done,
        .lcd_cmd_bits = 8,
        .lcd_param_bits = 8,
    };
    result = esp_lcd_new_panel_io_spi(
        (esp_lcd_spi_bus_handle_t)DISPLAY_HOST,
        &io_config,
        &context->panel_io);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Panel IO init failed: %s", esp_err_to_name(result));
        return result;
    }

    const esp_lcd_panel_dev_config_t panel_config = {
        .reset_gpio_num = NANA_DISPLAY_RESET_GPIO,
        .rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB,
        .bits_per_pixel = 16,
    };
    result = esp_lcd_new_panel_st7789(
        context->panel_io, &panel_config, &context->panel);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "ST7789 init failed: %s", esp_err_to_name(result));
        return result;
    }

    if ((result = esp_lcd_panel_reset(context->panel)) != ESP_OK ||
         (result = esp_lcd_panel_init(context->panel)) != ESP_OK ||
         (result = esp_lcd_panel_swap_xy(context->panel, true)) != ESP_OK ||
         (result = esp_lcd_panel_mirror(context->panel, false, true)) != ESP_OK ||
         (result = esp_lcd_panel_set_gap(
              context->panel, DISPLAY_X_GAP, 0)) != ESP_OK ||
        (result = esp_lcd_panel_invert_color(context->panel, true)) != ESP_OK ||
        (result = esp_lcd_panel_disp_on_off(context->panel, true)) != ESP_OK) {
        ESP_LOGE(TAG, "Panel startup failed: %s", esp_err_to_name(result));
        return result;
    }
    return ESP_OK;
}

esp_err_t nana_display_run_face_controller(void)
{
    display_context_t context = {0};
    const face_palette_t palette = face_palette();
    face_controller_ready = false;
    face_controller_start_result = ESP_ERR_INVALID_STATE;
    if (!face_tag_contract_valid()) {
        ESP_LOGE(TAG, "Face tag contract: FAIL");
        face_controller_start_result = ESP_ERR_INVALID_STATE;
        return ESP_ERR_INVALID_STATE;
    }
    ESP_LOGI(TAG,
             "Face tag contract: PASS | tags=%zu | expressions=%zu | "
             "gestures=%zu",
             sizeof(FACE_STAGES) / sizeof(FACE_STAGES[0]),
             face_tag_kind_count(NANA_FACE_TAG_EXPRESSION),
             face_tag_kind_count(NANA_FACE_TAG_GESTURE));

    if (face_command_queue != NULL) {
        ESP_LOGE(TAG, "Face controller already owns its command queue");
        face_controller_start_result = ESP_ERR_INVALID_STATE;
        return ESP_ERR_INVALID_STATE;
    }

    QueueHandle_t command_queue = xQueueCreate(
        FACE_COMMAND_QUEUE_LENGTH, sizeof(face_command_t));
    if (command_queue == NULL) {
        ESP_LOGE(TAG, "Face command queue allocation failed");
        face_controller_start_result = ESP_ERR_NO_MEM;
        return ESP_ERR_NO_MEM;
    }
    face_command_queue = command_queue;

    esp_err_t result = display_open(&context);
    if (result != ESP_OK) {
        display_close(&context);
        face_command_queue = NULL;
        vQueueDelete(command_queue);
        face_controller_start_result = result;
        return result;
    }

    result = clear_display(&context, palette.background);
    if (result != ESP_OK) {
        display_close(&context);
        face_command_queue = NULL;
        vQueueDelete(command_queue);
        face_controller_start_result = result;
        return result;
    }

    face_controller_start_result = ESP_OK;
    face_controller_ready = true;

    const face_stage_t *base_stage = face_stage_for_state(NANA_FACE_IDLE);
    const face_stage_t *active_stage = base_stage;
    bool active_is_event = false;
    int64_t active_started_ms = esp_timer_get_time() / 1000;
    int64_t report_started_ms = active_started_ms;
    uint32_t report_frames = 0;
    TickType_t next_frame = xTaskGetTickCount();

    ESP_LOGI(TAG,
             "Face controller: START | default=neutral | "
             "event_input=api_only | target_fps=20");
    ESP_LOGI(TAG, "face_state=neutral | source=base");

    while (true) {
        const int64_t now_ms = esp_timer_get_time() / 1000;

        face_command_t command;
        while (xQueueReceive(command_queue, &command, 0) == pdPASS) {
            const face_stage_t *requested_stage =
                face_stage_for_state(command.state);
            if (requested_stage == NULL) {
                ESP_LOGW(TAG, "Ignored invalid face command state=%d",
                         (int)command.state);
                continue;
            }

            if (command.kind == FACE_COMMAND_SET_EXPRESSION) {
                base_stage = requested_stage;
                ESP_LOGI(TAG, "face_base=%s", base_stage->name);
                if (!active_is_event) {
                    active_stage = base_stage;
                    active_started_ms = now_ms;
                    ESP_LOGI(TAG,
                             "face_state=%s | source=base",
                             active_stage->name);
                }
                continue;
            }

            active_stage = requested_stage;
            active_is_event = true;
            active_started_ms = now_ms;
            ESP_LOGI(TAG,
                     "face_state=%s | source=event | duration=%" PRIu32 "ms",
                     active_stage->name,
                     active_stage->duration_ms);
        }

        uint32_t local_time_ms = (uint32_t)(now_ms - active_started_ms);
        if (active_is_event &&
            local_time_ms >= active_stage->duration_ms) {
            ESP_LOGI(TAG,
                     "face_event_complete=%s | return_to=%s",
                     active_stage->name,
                     base_stage->name);
            active_stage = base_stage;
            active_is_event = false;
            active_started_ms = now_ms;
            local_time_ms = 0;
            ESP_LOGI(TAG,
                     "face_state=%s | source=base",
                     active_stage->name);
        }

        face_stage_t render_stage = *active_stage;
        if (!active_is_event) {
            render_stage.duration_ms = UINT32_MAX;
        }
        const face_scene_t scene = build_face_scene(
            &render_stage, local_time_ms);
        render_face(&context, &scene, &palette);
        result = transfer_face(&context);
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "Face frame failed: %s", esp_err_to_name(result));
            break;
        }

        ++report_frames;
        const int64_t report_elapsed_ms = now_ms - report_started_ms;
        if (report_elapsed_ms >= 5000) {
            const float fps =
                (float)report_frames * 1000.0f / (float)report_elapsed_ms;
            ESP_LOGI(TAG,
                     "animation_heartbeat | state=%s | source=%s | "
                     "fps=%.1f | free_heap=%" PRIu32,
                     active_stage->name,
                     active_is_event ? "event" : "base",
                     fps,
                     esp_get_free_heap_size());
            report_started_ms = now_ms;
            report_frames = 0;
        }

        vTaskDelayUntil(
            &next_frame, pdMS_TO_TICKS(DISPLAY_FRAME_INTERVAL_MS));
    }

    display_close(&context);
    face_controller_ready = false;
    face_command_queue = NULL;
    vQueueDelete(command_queue);
    return result;
}

static void face_controller_task_main(void *argument)
{
    (void)argument;
    const esp_err_t result = nana_display_run_face_controller();
    if (result != ESP_OK) {
        ESP_LOGE(TAG,
                 "Face controller task stopped: %s",
                 esp_err_to_name(result));
    }
    face_controller_task = NULL;
    vTaskDelete(NULL);
}

esp_err_t nana_display_start_face_controller(void)
{
    if (face_controller_task != NULL || face_command_queue != NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    face_controller_ready = false;
    face_controller_start_result = ESP_ERR_TIMEOUT;
    const BaseType_t created = xTaskCreate(
        face_controller_task_main,
        "nana-face",
        DISPLAY_TASK_STACK_BYTES,
        NULL,
        DISPLAY_TASK_PRIORITY,
        &face_controller_task);
    if (created != pdPASS) {
        face_controller_task = NULL;
        face_controller_start_result = ESP_ERR_NO_MEM;
        return ESP_ERR_NO_MEM;
    }

    const TickType_t deadline =
        xTaskGetTickCount() + pdMS_TO_TICKS(DISPLAY_START_TIMEOUT_MS);
    while (!face_controller_ready && face_controller_task != NULL &&
           xTaskGetTickCount() < deadline) {
        vTaskDelay(pdMS_TO_TICKS(10));
    }

    if (!face_controller_ready) {
        ESP_LOGE(TAG,
                 "Face controller did not become ready: %s",
                 esp_err_to_name(face_controller_start_result));
        return face_controller_start_result;
    }
    ESP_LOGI(TAG, "Face controller service: READY");
    return ESP_OK;
}
