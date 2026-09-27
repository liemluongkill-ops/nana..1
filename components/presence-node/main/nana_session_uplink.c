#include "nana_session_uplink.h"

#include <inttypes.h>
#include <math.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "esp_log.h"
#include "esp_rom_crc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "nana_microphone_vad.h"
#include "nana_session_audio.h"

#define NANA_SESSION_PROTOCOL "nana.presence.session.v1"

#define NANA_MEDIA_MAGIC "NPA1"
#define NANA_MEDIA_VERSION 1U
#define NANA_MEDIA_KIND_PCM_UPLINK 2U
#define NANA_MEDIA_HEADER_BYTES 24U

#define NANA_UPLINK_CAPTURE_MIN_MS 500U
#define NANA_UPLINK_CAPTURE_START_DELAY_MS 250U
#define NANA_UPLINK_RESPONSE_TIMEOUT_MS 15000U
#define NANA_UPLINK_TASK_STACK_BYTES 12288U
#define NANA_UPLINK_TASK_PRIORITY 5U
#define NANA_UPLINK_MAX_CREDITS 16U

#define UPLINK_CONNECTED_BIT BIT0
#define UPLINK_CANCELLED_BIT BIT1
#define UPLINK_READY_BIT BIT2
#define UPLINK_RECEIVED_BIT BIT3
#define UPLINK_TURN_COMPLETE_BIT BIT4
#define UPLINK_SUSPEND_REQUEST_BIT BIT5
#define UPLINK_SUSPENDED_BIT BIT6
#define UPLINK_SHUTDOWN_BIT BIT7

typedef enum {
    NANA_UPLINK_IDLE = 0,
    NANA_UPLINK_CAPTURING,
    NANA_UPLINK_WAIT_READY,
    NANA_UPLINK_SENDING,
    NANA_UPLINK_WAIT_RECEIVED,
    NANA_UPLINK_WAIT_TURN,
} nana_uplink_phase_t;

static const char *TAG = "nana_session_uplink";
static EventGroupHandle_t uplink_events = NULL;
static SemaphoreHandle_t uplink_credits = NULL;
static SemaphoreHandle_t uplink_stopped = NULL;
static TaskHandle_t uplink_task = NULL;
static nana_session_uplink_emit_text_fn emit_text_message = NULL;
static nana_session_uplink_emit_binary_fn emit_binary_message = NULL;
static void *emit_message_context = NULL;
static portMUX_TYPE uplink_lock = portMUX_INITIALIZER_UNLOCKED;
static volatile bool uplink_connected = false;
static volatile uint32_t connection_generation = 0U;
static volatile uint32_t worker_generation = 0U;
static volatile uint32_t active_stream_id = 0U;
static volatile nana_uplink_phase_t uplink_phase = NANA_UPLINK_IDLE;
static volatile bool uplink_suspend_requested = false;
static volatile bool uplink_shutdown_requested = false;
static uint32_t next_stream_id = 0U;

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

static void drain_credits(void)
{
    if (uplink_credits == NULL) {
        return;
    }
    while (xSemaphoreTake(uplink_credits, 0) == pdTRUE) {
    }
}

static bool connection_matches(uint32_t generation)
{
    bool matches = false;
    portENTER_CRITICAL(&uplink_lock);
    matches = uplink_connected && !uplink_shutdown_requested &&
              connection_generation == generation;
    portEXIT_CRITICAL(&uplink_lock);
    return matches;
}

static bool shutdown_requested(void)
{
    bool requested = false;
    portENTER_CRITICAL(&uplink_lock);
    requested = uplink_shutdown_requested;
    portEXIT_CRITICAL(&uplink_lock);
    return requested;
}

static bool microphone_capture_allowed(void)
{
    return connection_matches(worker_generation) && !uplink_suspend_requested;
}

static uint32_t begin_worker_generation(void)
{
    uint32_t generation = 0U;
    portENTER_CRITICAL(&uplink_lock);
    generation = connection_generation;
    worker_generation = generation;
    uplink_phase = NANA_UPLINK_CAPTURING;
    active_stream_id = 0U;
    portEXIT_CRITICAL(&uplink_lock);
    return generation;
}

static void set_active_turn(uint32_t stream_id, nana_uplink_phase_t phase)
{
    portENTER_CRITICAL(&uplink_lock);
    active_stream_id = stream_id;
    uplink_phase = phase;
    portEXIT_CRITICAL(&uplink_lock);
}

static void clear_active_turn(void)
{
    portENTER_CRITICAL(&uplink_lock);
    active_stream_id = 0U;
    uplink_phase = NANA_UPLINK_IDLE;
    portEXIT_CRITICAL(&uplink_lock);
}

static bool active_turn_matches(uint32_t stream_id)
{
    bool matches = false;
    portENTER_CRITICAL(&uplink_lock);
    matches = uplink_connected && stream_id != 0U &&
              active_stream_id == stream_id;
    portEXIT_CRITICAL(&uplink_lock);
    return matches;
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

static bool wait_for_event(
    uint32_t generation,
    EventBits_t event_bit,
    uint32_t timeout_ms)
{
    const TickType_t started_at = xTaskGetTickCount();
    while (connection_matches(generation)) {
        TickType_t wait_ticks = pdMS_TO_TICKS(250U);
        if (timeout_ms != 0U) {
            const TickType_t elapsed = xTaskGetTickCount() - started_at;
            const TickType_t timeout_ticks = pdMS_TO_TICKS(timeout_ms);
            if (elapsed >= timeout_ticks) {
                return false;
            }
            const TickType_t remaining = timeout_ticks - elapsed;
            if (wait_ticks > remaining) {
                wait_ticks = remaining;
            }
        }
        const EventBits_t bits = xEventGroupWaitBits(
            uplink_events,
            event_bit | UPLINK_CANCELLED_BIT | UPLINK_SHUTDOWN_BIT,
            pdTRUE,
            pdFALSE,
            wait_ticks);
        if ((bits & (UPLINK_CANCELLED_BIT | UPLINK_SHUTDOWN_BIT)) != 0U) {
            return false;
        }
        if ((bits & event_bit) != 0U) {
            return true;
        }
    }
    return false;
}

static bool take_credit(uint32_t generation)
{
    const TickType_t started_at = xTaskGetTickCount();
    while (connection_matches(generation)) {
        if (xSemaphoreTake(uplink_credits, pdMS_TO_TICKS(250U)) == pdTRUE) {
            return true;
        }
        if ((xTaskGetTickCount() - started_at) >=
            pdMS_TO_TICKS(NANA_UPLINK_RESPONSE_TIMEOUT_MS)) {
            return false;
        }
    }
    return false;
}

static uint32_t allocate_stream_id(void)
{
    ++next_stream_id;
    if (next_stream_id == 0U) {
        ++next_stream_id;
    }
    return next_stream_id;
}

static int clamp_dbfs_x10(double value)
{
    int result = (int)lround(value * 10.0);
    if (result < -1200) {
        result = -1200;
    } else if (result > 0) {
        result = 0;
    }
    return result;
}

static esp_err_t send_capture_begin(
    uint32_t stream_id,
    const nana_microphone_capture_t *capture)
{
    char message[384] = {0};
    const size_t total_bytes = capture->sample_count * sizeof(int16_t);
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"capture_begin\","
        "\"protocol\":\"%s\",\"stream_id\":%" PRIu32 ","
        "\"codec\":\"pcm_s16le\",\"sample_rate\":%u,"
        "\"channels\":1,\"sample_width\":2,\"chunk_bytes\":%u,"
        "\"max_bytes\":%u,\"integrity\":\"crc32_per_chunk\","
        "\"noise_dbfs_x10\":%d,\"start_dbfs_x10\":%d}",
        NANA_SESSION_PROTOCOL,
        stream_id,
        (unsigned)NANA_MICROPHONE_SAMPLE_RATE_HZ,
        (unsigned)NANA_SESSION_AUDIO_CHUNK_BYTES,
        (unsigned)total_bytes,
        clamp_dbfs_x10(capture->noise_dbfs),
        clamp_dbfs_x10(capture->start_dbfs));
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_text(message);
}

static esp_err_t send_capture_end(
    uint32_t stream_id,
    const nana_microphone_capture_t *capture,
    uint32_t chunks)
{
    char message[256] = {0};
    const size_t total_bytes = capture->sample_count * sizeof(int16_t);
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"capture_end\",\"protocol\":\"%s\","
        "\"stream_id\":%" PRIu32 ",\"total_bytes\":%u,"
        "\"chunks\":%" PRIu32 ",\"trim_samples\":%u,"
        "\"reason\":\"%s\"}",
        NANA_SESSION_PROTOCOL,
        stream_id,
        (unsigned)total_bytes,
        chunks,
        (unsigned)capture->trim_samples,
        capture->stop_reason);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_text(message);
}

static void send_capture_abort(uint32_t stream_id, const char *reason)
{
    if (stream_id == 0U || reason == NULL) {
        return;
    }
    char message[224] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"capture_abort\",\"protocol\":\"%s\","
        "\"stream_id\":%" PRIu32 ",\"reason\":\"%s\"}",
        NANA_SESSION_PROTOCOL,
        stream_id,
        reason);
    if (written > 0 && (size_t)written < sizeof(message)) {
        emit_text(message);
    }
}

static esp_err_t send_pcm_chunk(
    uint32_t stream_id,
    uint32_t sequence,
    const uint8_t *payload,
    size_t payload_bytes)
{
    if (payload == NULL || payload_bytes == 0U ||
        payload_bytes > NANA_SESSION_AUDIO_CHUNK_BYTES) {
        return ESP_ERR_INVALID_ARG;
    }
    uint8_t frame[NANA_MEDIA_HEADER_BYTES + NANA_SESSION_AUDIO_CHUNK_BYTES] = {0};
    memcpy(frame, NANA_MEDIA_MAGIC, 4U);
    frame[4] = NANA_MEDIA_VERSION;
    frame[5] = NANA_MEDIA_KIND_PCM_UPLINK;
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

static void uplink_worker(void *argument)
{
    (void)argument;
    while (true) {
        const EventBits_t wake_bits = xEventGroupWaitBits(
            uplink_events,
            UPLINK_CONNECTED_BIT | UPLINK_SHUTDOWN_BIT,
            pdFALSE,
            pdFALSE,
            portMAX_DELAY);
        if ((wake_bits & UPLINK_SHUTDOWN_BIT) != 0U || shutdown_requested()) {
            break;
        }

        if (uplink_suspend_requested) {
            clear_active_turn();
            xEventGroupSetBits(uplink_events, UPLINK_SUSPENDED_BIT);
            while (uplink_suspend_requested && !shutdown_requested()) {
                const EventBits_t bits = xEventGroupGetBits(uplink_events);
                if ((bits & UPLINK_CONNECTED_BIT) == 0U) {
                    break;
                }
                vTaskDelay(pdMS_TO_TICKS(20U));
            }
            xEventGroupClearBits(uplink_events, UPLINK_SUSPENDED_BIT);
            continue;
        }
        const uint32_t generation = begin_worker_generation();
        if (!connection_matches(generation)) {
            continue;
        }

        nana_microphone_capture_t capture = {0};
        const uint32_t stream_id = allocate_stream_id();
        esp_err_t result = nana_microphone_capture_vad_to_psram(
            stream_id,
            NANA_UPLINK_CAPTURE_MIN_MS,
            NANA_UPLINK_CAPTURE_START_DELAY_MS,
            "presence_session",
            "none",
            microphone_capture_allowed,
            &capture);
        if (result != ESP_OK || !connection_matches(generation)) {
            nana_microphone_capture_release(&capture);
            clear_active_turn();
            if (result != ESP_ERR_INVALID_STATE) {
                ESP_LOGE(TAG, "Microphone capture failed: %s", esp_err_to_name(result));
                vTaskDelay(pdMS_TO_TICKS(500U));
            }
            continue;
        }

        const size_t total_bytes = capture.sample_count * sizeof(int16_t);
        if (total_bytes == 0U || total_bytes > NANA_MICROPHONE_MAX_CAPTURE_BYTES ||
            capture.trim_samples > capture.sample_count) {
            ESP_LOGE(TAG, "Captured PCM violated the bounded contract");
            nana_microphone_capture_release(&capture);
            clear_active_turn();
            continue;
        }

        xEventGroupClearBits(
            uplink_events,
            UPLINK_READY_BIT | UPLINK_RECEIVED_BIT |
                UPLINK_TURN_COMPLETE_BIT);
        drain_credits();
        set_active_turn(stream_id, NANA_UPLINK_WAIT_READY);
        result = send_capture_begin(stream_id, &capture);
        if (result != ESP_OK ||
            !wait_for_event(
                generation,
                UPLINK_READY_BIT,
                NANA_UPLINK_RESPONSE_TIMEOUT_MS)) {
            ESP_LOGE(TAG, "Capture begin was not accepted: %s", esp_err_to_name(result));
            if (connection_matches(generation)) {
                send_capture_abort(stream_id, "capture_ready_timeout");
            }
            nana_microphone_capture_release(&capture);
            clear_active_turn();
            continue;
        }

        set_active_turn(stream_id, NANA_UPLINK_SENDING);
        const uint8_t *pcm = (const uint8_t *)capture.samples;
        size_t offset = 0U;
        uint32_t chunks = 0U;
        while (offset < total_bytes && connection_matches(generation)) {
            if (!take_credit(generation)) {
                result = ESP_ERR_TIMEOUT;
                break;
            }
            const size_t remaining = total_bytes - offset;
            const size_t payload_bytes =
                remaining < NANA_SESSION_AUDIO_CHUNK_BYTES
                    ? remaining
                    : NANA_SESSION_AUDIO_CHUNK_BYTES;
            result = send_pcm_chunk(
                stream_id, chunks, pcm + offset, payload_bytes);
            if (result != ESP_OK) {
                break;
            }
            offset += payload_bytes;
            ++chunks;
        }
        if (result != ESP_OK || offset != total_bytes ||
            !connection_matches(generation)) {
            ESP_LOGE(
                TAG,
                "Capture upload failed | stream=%" PRIu32
                " sent=%u/%uB result=%s",
                stream_id,
                (unsigned)offset,
                (unsigned)total_bytes,
                esp_err_to_name(result));
            if (connection_matches(generation)) {
                send_capture_abort(stream_id, "upload_failed");
            }
            nana_microphone_capture_release(&capture);
            clear_active_turn();
            continue;
        }

        set_active_turn(stream_id, NANA_UPLINK_WAIT_RECEIVED);
        result = send_capture_end(stream_id, &capture, chunks);
        if (result != ESP_OK ||
            !wait_for_event(
                generation,
                UPLINK_RECEIVED_BIT,
                NANA_UPLINK_RESPONSE_TIMEOUT_MS)) {
            ESP_LOGE(TAG, "Capture receipt timed out: %s", esp_err_to_name(result));
            if (connection_matches(generation)) {
                send_capture_abort(stream_id, "capture_receipt_timeout");
            }
            nana_microphone_capture_release(&capture);
            clear_active_turn();
            continue;
        }

        ESP_LOGI(
            TAG,
            "NANA_CAPTURE_UPLOADED stream=%" PRIu32
            " wire=%uB effective=%uB chunks=%" PRIu32,
            stream_id,
            (unsigned)total_bytes,
            (unsigned)(total_bytes - capture.trim_samples * sizeof(int16_t)),
            chunks);
        nana_microphone_capture_release(&capture);
        set_active_turn(stream_id, NANA_UPLINK_WAIT_TURN);

        // Core sends this only after STT/LLM and the remote speaker drain have
        // completed. No new RX I2S owner exists while the amplifier is active.
        wait_for_event(generation, UPLINK_TURN_COMPLETE_BIT, 0U);
        clear_active_turn();
        if (connection_matches(generation)) {
            vTaskDelay(pdMS_TO_TICKS(100U));
        }
    }
    clear_active_turn();
    drain_credits();
    uplink_task = NULL;
    xSemaphoreGive(uplink_stopped);
    vTaskDelete(NULL);
}

esp_err_t nana_session_uplink_init(
    nana_session_uplink_emit_text_fn emit_text_fn,
    nana_session_uplink_emit_binary_fn emit_binary_fn,
    void *context)
{
    if (emit_text_fn == NULL || emit_binary_fn == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    if (uplink_events != NULL && uplink_credits != NULL &&
        uplink_stopped != NULL && uplink_task != NULL) {
        emit_text_message = emit_text_fn;
        emit_binary_message = emit_binary_fn;
        emit_message_context = context;
        return ESP_OK;
    }

    EventGroupHandle_t events = xEventGroupCreate();
    SemaphoreHandle_t credits =
        xSemaphoreCreateCounting(NANA_UPLINK_MAX_CREDITS, 0U);
    SemaphoreHandle_t stopped = xSemaphoreCreateBinary();
    if (events == NULL || credits == NULL || stopped == NULL) {
        if (stopped != NULL) {
            vSemaphoreDelete(stopped);
        }
        if (credits != NULL) {
            vSemaphoreDelete(credits);
        }
        if (events != NULL) {
            vEventGroupDelete(events);
        }
        return ESP_ERR_NO_MEM;
    }
    uplink_events = events;
    uplink_credits = credits;
    uplink_stopped = stopped;
    emit_text_message = emit_text_fn;
    emit_binary_message = emit_binary_fn;
    emit_message_context = context;
    portENTER_CRITICAL(&uplink_lock);
    uplink_connected = false;
    uplink_suspend_requested = false;
    uplink_shutdown_requested = false;
    uplink_phase = NANA_UPLINK_IDLE;
    active_stream_id = 0U;
    portEXIT_CRITICAL(&uplink_lock);
    if (xTaskCreate(
            uplink_worker,
            "nana_audio_tx",
            NANA_UPLINK_TASK_STACK_BYTES,
            NULL,
            NANA_UPLINK_TASK_PRIORITY,
            &uplink_task) != pdPASS) {
        emit_text_message = NULL;
        emit_binary_message = NULL;
        emit_message_context = NULL;
        uplink_events = NULL;
        uplink_credits = NULL;
        uplink_stopped = NULL;
        vSemaphoreDelete(stopped);
        vSemaphoreDelete(credits);
        vEventGroupDelete(events);
        return ESP_ERR_NO_MEM;
    }
    return ESP_OK;
}

esp_err_t nana_session_uplink_deinit(uint32_t timeout_ms)
{
    if (timeout_ms == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    if (uplink_events == NULL) {
        return ESP_OK;
    }
    const bool task_running = uplink_task != NULL;
    nana_session_uplink_disconnected();
    portENTER_CRITICAL(&uplink_lock);
    uplink_shutdown_requested = true;
    ++connection_generation;
    portEXIT_CRITICAL(&uplink_lock);
    xEventGroupSetBits(
        uplink_events,
        UPLINK_CANCELLED_BIT | UPLINK_SHUTDOWN_BIT | UPLINK_CONNECTED_BIT);
    if (task_running &&
        xSemaphoreTake(
            uplink_stopped,
            pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }

    EventGroupHandle_t events = uplink_events;
    SemaphoreHandle_t credits = uplink_credits;
    SemaphoreHandle_t stopped = uplink_stopped;
    uplink_events = NULL;
    uplink_credits = NULL;
    uplink_stopped = NULL;
    uplink_task = NULL;
    emit_text_message = NULL;
    emit_binary_message = NULL;
    emit_message_context = NULL;
    if (credits != NULL) {
        vSemaphoreDelete(credits);
    }
    if (stopped != NULL) {
        vSemaphoreDelete(stopped);
    }
    vEventGroupDelete(events);
    return ESP_OK;
}

void nana_session_uplink_connected(void)
{
    if (uplink_events == NULL) {
        return;
    }
    if (shutdown_requested()) {
        return;
    }
    drain_credits();
    xEventGroupClearBits(
        uplink_events,
        UPLINK_CANCELLED_BIT | UPLINK_READY_BIT | UPLINK_RECEIVED_BIT |
            UPLINK_TURN_COMPLETE_BIT | UPLINK_SUSPEND_REQUEST_BIT |
            UPLINK_SUSPENDED_BIT | UPLINK_SHUTDOWN_BIT);
    portENTER_CRITICAL(&uplink_lock);
    ++connection_generation;
    if (connection_generation == 0U) {
        ++connection_generation;
    }
    uplink_connected = true;
    uplink_suspend_requested = false;
    portEXIT_CRITICAL(&uplink_lock);
    xEventGroupSetBits(uplink_events, UPLINK_CONNECTED_BIT);
}

void nana_session_uplink_disconnected(void)
{
    if (uplink_events == NULL) {
        return;
    }
    portENTER_CRITICAL(&uplink_lock);
    uplink_connected = false;
    uplink_suspend_requested = false;
    ++connection_generation;
    uplink_phase = NANA_UPLINK_IDLE;
    active_stream_id = 0U;
    portEXIT_CRITICAL(&uplink_lock);
    xEventGroupClearBits(
        uplink_events,
        UPLINK_CONNECTED_BIT | UPLINK_SUSPEND_REQUEST_BIT |
            UPLINK_SUSPENDED_BIT);
    xEventGroupSetBits(uplink_events, UPLINK_CANCELLED_BIT);
    drain_credits();
}

esp_err_t nana_session_uplink_suspend(uint32_t timeout_ms)
{
    if (uplink_events == NULL || timeout_ms == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    portENTER_CRITICAL(&uplink_lock);
    const bool connected = uplink_connected;
    uplink_suspend_requested = connected;
    portEXIT_CRITICAL(&uplink_lock);
    if (!connected) {
        return ESP_ERR_INVALID_STATE;
    }

    xEventGroupSetBits(uplink_events, UPLINK_SUSPEND_REQUEST_BIT);
    const EventBits_t bits = xEventGroupWaitBits(
        uplink_events,
        UPLINK_SUSPENDED_BIT | UPLINK_CANCELLED_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(timeout_ms));
    if ((bits & UPLINK_SUSPENDED_BIT) != 0U) {
        return ESP_OK;
    }
    nana_session_uplink_resume();
    return (bits & UPLINK_CANCELLED_BIT) != 0U
               ? ESP_ERR_INVALID_STATE
               : ESP_ERR_TIMEOUT;
}

void nana_session_uplink_resume(void)
{
    portENTER_CRITICAL(&uplink_lock);
    uplink_suspend_requested = false;
    portEXIT_CRITICAL(&uplink_lock);
    if (uplink_events != NULL) {
        xEventGroupClearBits(uplink_events, UPLINK_SUSPEND_REQUEST_BIT);
    }
}

esp_err_t nana_session_uplink_capture_ready(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t max_bytes)
{
    if (!active_turn_matches(stream_id) || credits == 0U ||
        credits > NANA_UPLINK_MAX_CREDITS ||
        max_bytes < NANA_MICROPHONE_MAX_CAPTURE_BYTES) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    drain_credits();
    for (uint32_t index = 0U; index < credits; ++index) {
        if (xSemaphoreGive(uplink_credits) != pdTRUE) {
            return ESP_ERR_INVALID_STATE;
        }
    }
    xEventGroupSetBits(uplink_events, UPLINK_READY_BIT);
    return ESP_OK;
}

esp_err_t nana_session_uplink_capture_credit(
    uint32_t stream_id,
    uint32_t credits,
    uint32_t bytes_received)
{
    (void)bytes_received;
    if (!active_turn_matches(stream_id) || credits == 0U ||
        credits > NANA_UPLINK_MAX_CREDITS) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    for (uint32_t index = 0U; index < credits; ++index) {
        if (xSemaphoreGive(uplink_credits) != pdTRUE) {
            return ESP_ERR_INVALID_STATE;
        }
    }
    return ESP_OK;
}

esp_err_t nana_session_uplink_capture_received(
    uint32_t stream_id,
    uint32_t wire_bytes,
    uint32_t effective_bytes,
    uint32_t chunks)
{
    if (!active_turn_matches(stream_id) || wire_bytes == 0U ||
        effective_bytes == 0U || effective_bytes > wire_bytes || chunks == 0U) {
        return ESP_ERR_INVALID_RESPONSE;
    }
    xEventGroupSetBits(uplink_events, UPLINK_RECEIVED_BIT);
    return ESP_OK;
}

esp_err_t nana_session_uplink_turn_complete(
    uint32_t stream_id,
    const char *status,
    const char *reason)
{
    if (!active_turn_matches(stream_id) || status == NULL || reason == NULL ||
        status[0] == '\0' || reason[0] == '\0') {
        return ESP_ERR_INVALID_RESPONSE;
    }
    ESP_LOGI(
        TAG,
        "NANA_TURN_COMPLETE stream=%" PRIu32 " status=%s reason=%s",
        stream_id,
        status,
        reason);
    xEventGroupSetBits(uplink_events, UPLINK_TURN_COMPLETE_BIT);
    return ESP_OK;
}
