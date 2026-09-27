#include "nana_session_camera.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_rom_crc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "nana_camera.h"
#include "nana_session_uplink.h"

#define NANA_SESSION_PROTOCOL "nana.presence.session.v1"

#define NANA_MEDIA_MAGIC "NPA1"
#define NANA_MEDIA_VERSION 1U
#define NANA_MEDIA_KIND_JPEG_UPLINK 3U
#define NANA_MEDIA_HEADER_BYTES 24U
#define NANA_CAMERA_CHUNK_BYTES 1024U
#define NANA_CAMERA_MAX_CREDITS 16U
#define NANA_CAMERA_RESPONSE_TIMEOUT_MS 15000U
#define NANA_CAMERA_MIC_SUSPEND_TIMEOUT_MS 3000U
#define NANA_CAMERA_TASK_STACK_BYTES 12288U
#define NANA_CAMERA_TASK_PRIORITY 5U

#define CAMERA_CONNECTED_BIT BIT0
#define CAMERA_CANCELLED_BIT BIT1
#define CAMERA_READY_BIT BIT2
#define CAMERA_RECEIVED_BIT BIT3
#define CAMERA_SHUTDOWN_BIT BIT4

typedef struct {
    uint32_t request_id;
    uint32_t max_bytes;
} nana_camera_request_t;

static const char *TAG = "nana_session_camera";
static EventGroupHandle_t camera_events = NULL;
static QueueHandle_t camera_requests = NULL;
static SemaphoreHandle_t camera_credits = NULL;
static SemaphoreHandle_t camera_stopped = NULL;
static TaskHandle_t camera_task = NULL;
static nana_session_camera_emit_text_fn emit_text_message = NULL;
static nana_session_camera_emit_binary_fn emit_binary_message = NULL;
static void *emit_message_context = NULL;
static portMUX_TYPE camera_lock = portMUX_INITIALIZER_UNLOCKED;
static volatile bool camera_connected = false;
static volatile uint32_t connection_generation = 0U;
static volatile uint32_t active_stream_id = 0U;
static volatile uint32_t active_max_bytes = 0U;
static volatile uint32_t active_total_bytes = 0U;
static volatile uint32_t active_chunks = 0U;
static volatile bool active_cancel_requested = false;
static volatile bool camera_shutdown_requested = false;

static void write_be16(uint8_t *output, uint16_t value)
{
    output[0] = (uint8_t)(value >> 8U);
    output[1] = (uint8_t)value;
}

static void write_be32(uint8_t *output, uint32_t value)
{
    output[0] = (uint8_t)(value >> 24U);
    output[1] = (uint8_t)(value >> 16U);
    output[2] = (uint8_t)(value >> 8U);
    output[3] = (uint8_t)value;
}

static esp_err_t emit_text(const char *message)
{
    if (emit_text_message == NULL || message == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    return emit_text_message(message, emit_message_context);
}

static esp_err_t emit_binary(const uint8_t *message, size_t message_bytes)
{
    if (emit_binary_message == NULL || message == NULL || message_bytes == 0U) {
        return ESP_ERR_INVALID_STATE;
    }
    return emit_binary_message(message, message_bytes, emit_message_context);
}

static bool connection_matches(uint32_t generation)
{
    bool matches = false;
    portENTER_CRITICAL(&camera_lock);
    matches = camera_connected && !camera_shutdown_requested &&
              connection_generation == generation;
    portEXIT_CRITICAL(&camera_lock);
    return matches;
}

static bool shutdown_requested(void)
{
    bool requested = false;
    portENTER_CRITICAL(&camera_lock);
    requested = camera_shutdown_requested;
    portEXIT_CRITICAL(&camera_lock);
    return requested;
}

static bool active_stream_matches(uint32_t stream_id)
{
    bool matches = false;
    portENTER_CRITICAL(&camera_lock);
    matches = camera_connected && stream_id != 0U &&
              active_stream_id == stream_id;
    portEXIT_CRITICAL(&camera_lock);
    return matches;
}

static bool active_stream_cancelled(uint32_t stream_id)
{
    bool cancelled = false;
    portENTER_CRITICAL(&camera_lock);
    cancelled = camera_connected && stream_id != 0U &&
                active_stream_id == stream_id && active_cancel_requested;
    portEXIT_CRITICAL(&camera_lock);
    return cancelled;
}

static void set_active_stream(
    uint32_t stream_id,
    uint32_t max_bytes,
    uint32_t total_bytes,
    uint32_t chunks)
{
    portENTER_CRITICAL(&camera_lock);
    active_stream_id = stream_id;
    active_max_bytes = max_bytes;
    active_total_bytes = total_bytes;
    active_chunks = chunks;
    portEXIT_CRITICAL(&camera_lock);
}

static void clear_active_stream(void)
{
    portENTER_CRITICAL(&camera_lock);
    active_stream_id = 0U;
    active_max_bytes = 0U;
    active_total_bytes = 0U;
    active_chunks = 0U;
    active_cancel_requested = false;
    portEXIT_CRITICAL(&camera_lock);
}

static void drain_credits(void)
{
    if (camera_credits == NULL) {
        return;
    }
    while (xSemaphoreTake(camera_credits, 0) == pdTRUE) {
    }
}

static bool wait_for_event(
    uint32_t generation,
    EventBits_t expected,
    uint32_t timeout_ms)
{
    const TickType_t started_at = xTaskGetTickCount();
    while (connection_matches(generation)) {
        const TickType_t elapsed = xTaskGetTickCount() - started_at;
        const TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
        if (elapsed >= timeout_ticks) {
            return false;
        }
        TickType_t wait_ticks = pdMS_TO_TICKS(250U);
        if (wait_ticks > timeout_ticks - elapsed) {
            wait_ticks = timeout_ticks - elapsed;
        }
        const EventBits_t bits = xEventGroupWaitBits(
            camera_events,
            expected | CAMERA_CANCELLED_BIT | CAMERA_SHUTDOWN_BIT,
            pdTRUE,
            pdFALSE,
            wait_ticks);
        if ((bits & (CAMERA_CANCELLED_BIT | CAMERA_SHUTDOWN_BIT)) != 0U) {
            return false;
        }
        if ((bits & expected) != 0U) {
            return true;
        }
    }
    return false;
}

static bool take_credit(uint32_t generation)
{
    const TickType_t started_at = xTaskGetTickCount();
    while (connection_matches(generation)) {
        portENTER_CRITICAL(&camera_lock);
        const bool cancelled = active_cancel_requested;
        portEXIT_CRITICAL(&camera_lock);
        if (cancelled) {
            return false;
        }
        if (xSemaphoreTake(camera_credits, pdMS_TO_TICKS(250U)) == pdTRUE) {
            return true;
        }
        if ((xTaskGetTickCount() - started_at) >=
            pdMS_TO_TICKS(NANA_CAMERA_RESPONSE_TIMEOUT_MS)) {
            return false;
        }
    }
    return false;
}

static void send_camera_abort(uint32_t stream_id, const char *reason)
{
    if (stream_id == 0U || reason == NULL) {
        return;
    }
    char message[224] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"camera_abort\",\"protocol\":\"%s\","
        "\"stream_id\":%" PRIu32 ",\"reason\":\"%s\"}",
        NANA_SESSION_PROTOCOL,
        stream_id,
        reason);
    if (written > 0 && (size_t)written < sizeof(message)) {
        emit_text(message);
    }
}

static esp_err_t send_camera_cancelled(uint32_t stream_id)
{
    char message[192] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"camera_cancelled\",\"protocol\":\"%s\","
        "\"stream_id\":%" PRIu32 ",\"reason\":\"core_cancelled\"}",
        NANA_SESSION_PROTOCOL,
        stream_id);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_text(message);
}

static esp_err_t send_camera_begin(
    uint32_t stream_id,
    const camera_fb_t *frame)
{
    char message[320] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"camera_begin\",\"protocol\":\"%s\","
        "\"stream_id\":%" PRIu32 ",\"codec\":\"jpeg\","
        "\"width\":%u,\"height\":%u,\"total_bytes\":%u,"
        "\"chunk_bytes\":%u,\"integrity\":\"crc32_per_chunk\"}",
        NANA_SESSION_PROTOCOL,
        stream_id,
        (unsigned)frame->width,
        (unsigned)frame->height,
        (unsigned)frame->len,
        (unsigned)NANA_CAMERA_CHUNK_BYTES);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_text(message);
}

static esp_err_t send_camera_end(
    uint32_t stream_id,
    uint32_t total_bytes,
    uint32_t chunks)
{
    char message[224] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"camera_end\",\"protocol\":\"%s\","
        "\"stream_id\":%" PRIu32 ",\"total_bytes\":%" PRIu32 ","
        "\"chunks\":%" PRIu32 "}",
        NANA_SESSION_PROTOCOL,
        stream_id,
        total_bytes,
        chunks);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_text(message);
}

static esp_err_t send_jpeg_chunk(
    uint32_t stream_id,
    uint32_t sequence,
    const uint8_t *payload,
    size_t payload_bytes)
{
    if (payload == NULL || payload_bytes == 0U ||
        payload_bytes > NANA_CAMERA_CHUNK_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t frame[NANA_MEDIA_HEADER_BYTES + NANA_CAMERA_CHUNK_BYTES] = {0};
    memcpy(frame, NANA_MEDIA_MAGIC, 4U);
    frame[4] = NANA_MEDIA_VERSION;
    frame[5] = NANA_MEDIA_KIND_JPEG_UPLINK;
    write_be16(frame + 6U, 0U);
    write_be32(frame + 8U, stream_id);
    write_be32(frame + 12U, sequence);
    write_be32(frame + 16U, (uint32_t)payload_bytes);
    write_be32(
        frame + 20U,
        esp_rom_crc32_le(0U, payload, (uint32_t)payload_bytes));
    memcpy(frame + NANA_MEDIA_HEADER_BYTES, payload, payload_bytes);
    return emit_binary(frame, NANA_MEDIA_HEADER_BYTES + payload_bytes);
}

static void camera_worker(void *argument)
{
    (void)argument;
    while (true) {
        nana_camera_request_t request = {0};
        if (xQueueReceive(camera_requests, &request, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (shutdown_requested()) {
            break;
        }

        uint32_t generation = 0U;
        portENTER_CRITICAL(&camera_lock);
        generation = connection_generation;
        portEXIT_CRITICAL(&camera_lock);
        if (!connection_matches(generation)) {
            continue;
        }

        if (!active_stream_matches(request.request_id)) {
            continue;
        }

        bool mic_suspended = false;
        camera_fb_t *frame = NULL;
        char failure_reason_detail[96] = {0};
        const char *failure_reason = NULL;
        if (active_stream_cancelled(request.request_id)) {
            goto cleanup;
        }
        esp_err_t result = nana_session_uplink_suspend(
            NANA_CAMERA_MIC_SUSPEND_TIMEOUT_MS);
        if (result != ESP_OK) {
            failure_reason = "mic_suspend_timeout";
            goto cleanup;
        }
        mic_suspended = true;
        if (active_stream_cancelled(request.request_id)) {
            goto cleanup;
        }

        result = nana_camera_start();
        if (result != ESP_OK) {
            const int written = snprintf(
                failure_reason_detail,
                sizeof(failure_reason_detail),
                "camera_start_failed_%s_0x%x",
                esp_err_to_name(result),
                (unsigned)result);
            failure_reason =
                written > 0 && (size_t)written < sizeof(failure_reason_detail)
                    ? failure_reason_detail
                    : "camera_start_failed_unknown";
            goto cleanup;
        }
        frame = nana_camera_acquire_frame(pdMS_TO_TICKS(3000U));
        if (frame == NULL) {
            failure_reason = "frame_timeout";
            goto cleanup;
        }
        if (active_stream_cancelled(request.request_id)) {
            goto cleanup;
        }
        if (!nana_camera_frame_is_complete_jpeg(frame)) {
            failure_reason = "invalid_jpeg";
            goto cleanup;
        }
        if (frame->width != NANA_CAMERA_FRAME_WIDTH ||
            frame->height != NANA_CAMERA_FRAME_HEIGHT ||
            frame->len > request.max_bytes ||
            frame->len > NANA_CAMERA_MAX_JPEG_BYTES) {
            failure_reason = "frame_out_of_bounds";
            goto cleanup;
        }

        xEventGroupClearBits(
            camera_events,
            CAMERA_READY_BIT | CAMERA_RECEIVED_BIT);
        drain_credits();
        set_active_stream(
            request.request_id,
            request.max_bytes,
            (uint32_t)frame->len,
            0U);
        result = send_camera_begin(request.request_id, frame);
        if (result != ESP_OK ||
            !wait_for_event(
                generation,
                CAMERA_READY_BIT,
                NANA_CAMERA_RESPONSE_TIMEOUT_MS)) {
            if (!active_stream_cancelled(request.request_id)) {
                failure_reason = "camera_ready_timeout";
            }
            goto cleanup;
        }

        size_t offset = 0U;
        uint32_t chunks = 0U;
        while (offset < frame->len && connection_matches(generation)) {
            if (!take_credit(generation)) {
                result = ESP_ERR_TIMEOUT;
                break;
            }
            const size_t remaining = frame->len - offset;
            const size_t payload_bytes =
                remaining < NANA_CAMERA_CHUNK_BYTES
                    ? remaining
                    : NANA_CAMERA_CHUNK_BYTES;
            result = send_jpeg_chunk(
                request.request_id,
                chunks,
                frame->buf + offset,
                payload_bytes);
            if (result != ESP_OK) {
                break;
            }
            offset += payload_bytes;
            ++chunks;
        }
        if (result != ESP_OK || offset != frame->len ||
            !connection_matches(generation)) {
            if (!active_stream_cancelled(request.request_id)) {
                failure_reason = "upload_failed";
            }
            goto cleanup;
        }

        set_active_stream(
            request.request_id,
            request.max_bytes,
            (uint32_t)frame->len,
            chunks);
        result = send_camera_end(
            request.request_id,
            (uint32_t)frame->len,
            chunks);
        if (result != ESP_OK ||
            !wait_for_event(
                generation,
                CAMERA_RECEIVED_BIT,
                NANA_CAMERA_RESPONSE_TIMEOUT_MS)) {
            if (!active_stream_cancelled(request.request_id)) {
                failure_reason = "camera_receipt_timeout";
            }
            goto cleanup;
        }

        ESP_LOGI(
            TAG,
            "NANA_CAMERA_UPLOADED stream=%" PRIu32
            " bytes=%u chunks=%" PRIu32 " frame=%ux%u",
            request.request_id,
            (unsigned)frame->len,
            chunks,
            (unsigned)frame->width,
            (unsigned)frame->height);

    cleanup:
        if (failure_reason != NULL && connection_matches(generation)) {
            ESP_LOGW(
                TAG,
                "Camera snapshot failed | stream=%" PRIu32 " reason=%s",
                request.request_id,
                failure_reason);
            send_camera_abort(request.request_id, failure_reason);
        }
        if (frame != NULL) {
            nana_camera_release_frame(frame);
        }
        const esp_err_t stop_result = nana_camera_stop();
        if (stop_result != ESP_OK) {
            ESP_LOGE(TAG, "Camera stop failed: %s", esp_err_to_name(stop_result));
        }
        clear_active_stream();
        drain_credits();
        if (mic_suspended) {
            nana_session_uplink_resume();
        }
    }
    nana_camera_stop();
    clear_active_stream();
    drain_credits();
    nana_session_uplink_resume();
    camera_task = NULL;
    xSemaphoreGive(camera_stopped);
    vTaskDelete(NULL);
}

esp_err_t nana_session_camera_init(
    nana_session_camera_emit_text_fn emit_text_fn,
    nana_session_camera_emit_binary_fn emit_binary_fn,
    void *context)
{
    if (emit_text_fn == NULL || emit_binary_fn == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (camera_events != NULL && camera_requests != NULL &&
        camera_credits != NULL && camera_stopped != NULL &&
        camera_task != NULL) {
        emit_text_message = emit_text_fn;
        emit_binary_message = emit_binary_fn;
        emit_message_context = context;
        return ESP_OK;
    }

    EventGroupHandle_t events = xEventGroupCreate();
    QueueHandle_t requests = xQueueCreate(1U, sizeof(nana_camera_request_t));
    SemaphoreHandle_t credits =
        xSemaphoreCreateCounting(NANA_CAMERA_MAX_CREDITS, 0U);
    SemaphoreHandle_t stopped = xSemaphoreCreateBinary();
    if (events == NULL || requests == NULL || credits == NULL ||
        stopped == NULL) {
        if (stopped != NULL) {
            vSemaphoreDelete(stopped);
        }
        if (credits != NULL) {
            vSemaphoreDelete(credits);
        }
        if (requests != NULL) {
            vQueueDelete(requests);
        }
        if (events != NULL) {
            vEventGroupDelete(events);
        }
        return ESP_ERR_NO_MEM;
    }
    camera_events = events;
    camera_requests = requests;
    camera_credits = credits;
    camera_stopped = stopped;
    emit_text_message = emit_text_fn;
    emit_binary_message = emit_binary_fn;
    emit_message_context = context;
    portENTER_CRITICAL(&camera_lock);
    camera_connected = false;
    camera_shutdown_requested = false;
    active_cancel_requested = false;
    active_stream_id = 0U;
    portEXIT_CRITICAL(&camera_lock);
    if (xTaskCreate(
            camera_worker,
            "nana_camera_tx",
            NANA_CAMERA_TASK_STACK_BYTES,
            NULL,
            NANA_CAMERA_TASK_PRIORITY,
            &camera_task) != pdPASS) {
        emit_text_message = NULL;
        emit_binary_message = NULL;
        emit_message_context = NULL;
        camera_events = NULL;
        camera_requests = NULL;
        camera_credits = NULL;
        camera_stopped = NULL;
        vSemaphoreDelete(stopped);
        vSemaphoreDelete(credits);
        vQueueDelete(requests);
        vEventGroupDelete(events);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t nana_session_camera_deinit(uint32_t timeout_ms)
{
    if (timeout_ms == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    if (camera_events == NULL) {
        return ESP_OK;
    }
    const bool task_running = camera_task != NULL;
    nana_session_camera_disconnected();
    portENTER_CRITICAL(&camera_lock);
    camera_shutdown_requested = true;
    ++connection_generation;
    portEXIT_CRITICAL(&camera_lock);
    xEventGroupSetBits(
        camera_events,
        CAMERA_CANCELLED_BIT | CAMERA_SHUTDOWN_BIT | CAMERA_CONNECTED_BIT);
    xQueueReset(camera_requests);
    const nana_camera_request_t shutdown = {0};
    if (xQueueSend(camera_requests, &shutdown, 0) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    if (task_running &&
        xSemaphoreTake(
            camera_stopped,
            pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    EventGroupHandle_t events = camera_events;
    QueueHandle_t requests = camera_requests;
    SemaphoreHandle_t credits = camera_credits;
    SemaphoreHandle_t stopped = camera_stopped;
    camera_events = NULL;
    camera_requests = NULL;
    camera_credits = NULL;
    camera_stopped = NULL;
    camera_task = NULL;
    emit_text_message = NULL;
    emit_binary_message = NULL;
    emit_message_context = NULL;
    if (stopped != NULL) {
        vSemaphoreDelete(stopped);
    }
    if (credits != NULL) {
        vSemaphoreDelete(credits);
    }
    if (requests != NULL) {
        vQueueDelete(requests);
    }
    vEventGroupDelete(events);
    return ESP_OK;
}

bool nana_session_camera_available(void)
{
    return camera_events != NULL && camera_requests != NULL &&
           camera_credits != NULL && camera_stopped != NULL &&
           camera_task != NULL;
}

void nana_session_camera_connected(void)
{
    if (!nana_session_camera_available()) {
        return;
    }
    if (shutdown_requested()) {
        return;
    }
    xQueueReset(camera_requests);
    drain_credits();
    xEventGroupClearBits(
        camera_events,
        CAMERA_CANCELLED_BIT | CAMERA_READY_BIT | CAMERA_RECEIVED_BIT |
            CAMERA_SHUTDOWN_BIT);
    portENTER_CRITICAL(&camera_lock);
    ++connection_generation;
    if (connection_generation == 0U) {
        ++connection_generation;
    }
    camera_connected = true;
    active_cancel_requested = false;
    portEXIT_CRITICAL(&camera_lock);
    clear_active_stream();
    xEventGroupSetBits(camera_events, CAMERA_CONNECTED_BIT);
}

void nana_session_camera_disconnected(void)
{
    if (!nana_session_camera_available()) {
        return;
    }
    portENTER_CRITICAL(&camera_lock);
    camera_connected = false;
    active_cancel_requested = true;
    ++connection_generation;
    portEXIT_CRITICAL(&camera_lock);
    xQueueReset(camera_requests);
    xEventGroupClearBits(camera_events, CAMERA_CONNECTED_BIT);
    xEventGroupSetBits(camera_events, CAMERA_CANCELLED_BIT);
    drain_credits();
    clear_active_stream();
    nana_session_uplink_resume();
}

esp_err_t nana_session_camera_request(uint32_t request_id, uint32_t max_bytes)
{
    if (request_id == 0U || max_bytes < 4096U ||
        max_bytes > NANA_CAMERA_MAX_JPEG_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    if (!nana_session_camera_available()) {
        return ESP_ERR_INVALID_STATE;
    }
    bool accepted = false;
    portENTER_CRITICAL(&camera_lock);
    if (camera_connected && active_stream_id == 0U) {
        active_stream_id = request_id;
        active_max_bytes = max_bytes;
        active_total_bytes = 0U;
        active_chunks = 0U;
        active_cancel_requested = false;
        accepted = true;
    }
    portEXIT_CRITICAL(&camera_lock);
    if (!accepted) {
        send_camera_abort(request_id, "camera_busy");
        return ESP_OK;
    }
    xEventGroupClearBits(
        camera_events,
        CAMERA_CANCELLED_BIT | CAMERA_READY_BIT | CAMERA_RECEIVED_BIT);
    drain_credits();
    nana_camera_request_t request = {
        .request_id = request_id,
        .max_bytes = max_bytes,
    };
    if (xQueueSend(camera_requests, &request, 0) != pdTRUE) {
        clear_active_stream();
        send_camera_abort(request_id, "camera_busy");
    }
    return ESP_OK;
}

esp_err_t nana_session_camera_cancel(uint32_t stream_id)
{
    if (!active_stream_matches(stream_id)) {
        // Cancellation is idempotent so a late timeout cannot tear down the
        // otherwise healthy Presence session.
        return ESP_OK;
    }
    portENTER_CRITICAL(&camera_lock);
    active_cancel_requested = true;
    portEXIT_CRITICAL(&camera_lock);
    const esp_err_t result = send_camera_cancelled(stream_id);
    xEventGroupSetBits(camera_events, CAMERA_CANCELLED_BIT);
    return result;
}

esp_err_t nana_session_camera_ready(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t max_bytes)
{
    if (!active_stream_matches(stream_id) || credits == 0U ||
        credits > NANA_CAMERA_MAX_CREDITS || max_bytes != active_max_bytes ||
        active_total_bytes == 0U || active_total_bytes > max_bytes) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    drain_credits();
    for (uint32_t index = 0U; index < credits; ++index) {
        if (xSemaphoreGive(camera_credits) != pdTRUE) {
            return ESP_ERR_INVALID_STATE;
        }
    }
    xEventGroupSetBits(camera_events, CAMERA_READY_BIT);
    return ESP_OK;
}

esp_err_t nana_session_camera_credit(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t bytes_received)
{
    if (!active_stream_matches(stream_id) || credits == 0U ||
        credits > NANA_CAMERA_MAX_CREDITS ||
        bytes_received > active_total_bytes) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    for (uint32_t index = 0U; index < credits; ++index) {
        if (xSemaphoreGive(camera_credits) != pdTRUE) {
            return ESP_ERR_INVALID_STATE;
        }
    }
    return ESP_OK;
}

esp_err_t nana_session_camera_received(
    uint32_t stream_id,
    uint32_t total_bytes,
    uint32_t chunks)
{
    if (!active_stream_matches(stream_id) ||
        total_bytes != active_total_bytes || chunks != active_chunks ||
        chunks == 0U) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    xEventGroupSetBits(camera_events, CAMERA_RECEIVED_BIT);
    return ESP_OK;
}
