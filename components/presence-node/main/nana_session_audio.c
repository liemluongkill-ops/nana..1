#include "nana_session_audio.h"

#include <inttypes.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_rom_crc.h"
#include "freertos/FreeRTOS.h"
#include "freertos/queue.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#include "nana_speaker.h"

#define NANA_MEDIA_MAGIC "NPA1"
#define NANA_MEDIA_VERSION 1U
#define NANA_MEDIA_KIND_PCM_DOWNLINK 1U
#define NANA_MEDIA_HEADER_BYTES 24U
#define NANA_MEDIA_MAX_PLAYBACK_BYTES (4U * 1024U * 1024U)

#define NANA_AUDIO_QUEUE_DEPTH 10U
#define NANA_AUDIO_ADVERTISED_CREDITS 8U
#define NANA_AUDIO_PREBUFFER_CHUNKS 8U
#define NANA_AUDIO_CHUNK_TIMEOUT_MS 3500U
#define NANA_AUDIO_TASK_STACK_BYTES 6144U
#define NANA_AUDIO_TASK_PRIORITY 4U
#define NANA_AUDIO_GAIN_Q15 32768

_Static_assert(
    NANA_AUDIO_QUEUE_DEPTH >= NANA_AUDIO_ADVERTISED_CREDITS + 2U,
    "audio queue must reserve control slots beyond advertised media credits");
_Static_assert(
    NANA_AUDIO_PREBUFFER_CHUNKS <= NANA_AUDIO_ADVERTISED_CREDITS,
    "audio prebuffer must fit within the initial credit window");

typedef enum {
    NANA_AUDIO_MESSAGE_BEGIN = 1,
    NANA_AUDIO_MESSAGE_CHUNK,
    NANA_AUDIO_MESSAGE_END,
    NANA_AUDIO_MESSAGE_ABORT,
    NANA_AUDIO_MESSAGE_SHUTDOWN,
} nana_audio_message_type_t;

typedef struct {
    nana_audio_message_type_t type;
    uint32_t stream_id;
    uint32_t sequence;
    uint32_t sample_rate;
    uint32_t channels;
    uint32_t sample_width;
    uint32_t total_bytes;
    uint32_t total_samples;
    uint32_t chunk_bytes;
    uint32_t chunks;
    bool streaming;
    uint16_t payload_bytes;
    char reason[32];
    uint8_t payload[NANA_SESSION_AUDIO_CHUNK_BYTES];
} nana_audio_message_t;

typedef struct {
    bool active;
    uint32_t stream_id;
    uint32_t total_bytes;
    uint32_t total_samples;
    uint32_t total_chunks;
    uint32_t expected_sequence;
    uint32_t bytes_received;
    uint32_t bytes_played;
    uint32_t queue_high_water;
    uint32_t underruns;
    bool streaming;
    bool playback_started;
    uint8_t *pcm_buffer;
    nana_speaker_stream_t *speaker;
} nana_audio_stream_state_t;

static const char *TAG = "nana_session_audio";
static QueueHandle_t audio_queue = NULL;
static StaticQueue_t audio_queue_control;
static uint8_t audio_queue_storage[
    NANA_AUDIO_QUEUE_DEPTH * sizeof(nana_audio_message_t)];
static StaticTask_t audio_task_control;
static StackType_t audio_task_stack[
    NANA_AUDIO_TASK_STACK_BYTES / sizeof(StackType_t)];
static TaskHandle_t audio_task = NULL;
static SemaphoreHandle_t audio_stopped = NULL;
static StaticSemaphore_t audio_stopped_control;
static nana_session_audio_emit_fn emit_message = NULL;
static nana_session_audio_credit_fn emit_credit_message = NULL;
static void *emit_context = NULL;

static uint32_t read_be32(const uint8_t *value)
{
    return ((uint32_t)value[0] << 24U) |
           ((uint32_t)value[1] << 16U) |
           ((uint32_t)value[2] << 8U) |
           (uint32_t)value[3];
}

static esp_err_t emit_json(const char *message)
{
    if (emit_message == NULL || message == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    return emit_message(message, emit_context);
}

static esp_err_t emit_ready(uint32_t stream_id)
{
    char message[224] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"playback_ready\","
        "\"protocol\":\"nana.presence.session.v1\","
        "\"stream_id\":%" PRIu32 ",\"credits\":%u,"
        "\"queue_capacity\":%u}",
        stream_id,
        (unsigned)NANA_AUDIO_ADVERTISED_CREDITS,
        (unsigned)NANA_AUDIO_ADVERTISED_CREDITS);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_json(message);
}

static esp_err_t emit_credit(
    uint32_t stream_id,
    uint32_t bytes_played)
{
    if (emit_credit_message != NULL) {
        return emit_credit_message(stream_id, bytes_played, emit_context);
    }
    char message[224] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"playback_credit\","
        "\"protocol\":\"nana.presence.session.v1\","
        "\"stream_id\":%" PRIu32 ",\"credits\":1,"
        "\"bytes_played\":%" PRIu32 "}",
        stream_id,
        bytes_played);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_json(message);
}

static esp_err_t emit_playback_started(
    uint32_t stream_id,
    uint32_t bytes_buffered)
{
    char message[240] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"playback_started\","
        "\"protocol\":\"nana.presence.session.v1\","
        "\"stream_id\":%" PRIu32 ",\"bytes_buffered\":%" PRIu32 "}",
        stream_id,
        bytes_buffered);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_json(message);
}

static esp_err_t emit_drained(
    const nana_audio_stream_state_t *state,
    const char *status)
{
    char message[320] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"playback_drained\","
        "\"protocol\":\"nana.presence.session.v1\","
        "\"stream_id\":%" PRIu32 ",\"status\":\"%s\","
        "\"bytes_received\":%" PRIu32 ","
        "\"bytes_played\":%" PRIu32 ","
        "\"queue_high_water\":%" PRIu32 ","
        "\"underruns\":%" PRIu32 "}",
        state->stream_id,
        status,
        state->bytes_received,
        state->bytes_played,
        state->queue_high_water,
        state->underruns);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return emit_json(message);
}

static void reset_stream(nana_audio_stream_state_t *state)
{
    heap_caps_free(state->pcm_buffer);
    memset(state, 0, sizeof(*state));
}

static void abort_stream(
    nana_audio_stream_state_t *state,
    const char *status,
    bool count_underrun)
{
    if (!state->active) {
        nana_speaker_mute();
        return;
    }
    if (count_underrun) {
        ++state->underruns;
    }
    if (state->speaker != NULL) {
        nana_speaker_stream_abort(state->speaker);
        state->speaker = NULL;
    } else {
        nana_speaker_mute();
    }
    ESP_LOGE(TAG,
             "NANA_PLAYBACK_DRAINED stream=%" PRIu32
             " status=%s received=%" PRIu32 " played=%" PRIu32
             " underruns=%" PRIu32,
             state->stream_id,
             status,
             state->bytes_received,
             state->bytes_played,
             state->underruns);
    if (emit_drained(state, status) != ESP_OK) {
        ESP_LOGE(TAG, "Could not queue playback_drained");
    }
    reset_stream(state);
}

static esp_err_t play_buffered_audio(
    nana_audio_stream_state_t *state,
    uint32_t bytes_available)
{
    while (state->bytes_played < bytes_available) {
        const size_t remaining =
            (size_t)bytes_available - state->bytes_played;
        const size_t write_bytes =
            remaining < NANA_SESSION_AUDIO_CHUNK_BYTES
                ? remaining
                : NANA_SESSION_AUDIO_CHUNK_BYTES;
        const esp_err_t result = nana_speaker_stream_write(
            state->speaker,
            (const int16_t *)(state->pcm_buffer + state->bytes_played),
            write_bytes / sizeof(int16_t));
        if (result != ESP_OK) {
            return result;
        }
        state->bytes_played += (uint32_t)write_bytes;
    }
    return ESP_OK;
}

static esp_err_t start_playback_if_ready(
    nana_audio_stream_state_t *state,
    const nana_audio_message_t *begin,
    bool force)
{
    if (state->playback_started) {
        return ESP_OK;
    }

    const uint32_t prebuffer_bytes =
        NANA_AUDIO_PREBUFFER_CHUNKS * NANA_SESSION_AUDIO_CHUNK_BYTES;
    if (!force && state->bytes_received <
                       (state->total_bytes < prebuffer_bytes
                            ? state->total_bytes
                            : prebuffer_bytes)) {
        return ESP_OK;
    }

    const esp_err_t result = nana_speaker_stream_begin(
        begin->sample_rate,
        begin->total_samples,
        NANA_AUDIO_GAIN_Q15,
        &state->speaker);
    if (result != ESP_OK) {
        return result;
    }
    state->playback_started = true;
    ESP_LOGI(TAG,
             "NANA_PLAYBACK_STARTED stream=%" PRIu32
             " prebuffer=%" PRIu32 "B total=%" PRIu32 "B",
             state->stream_id,
             state->bytes_received,
             state->total_bytes);
    if (emit_playback_started(state->stream_id, state->bytes_received) != ESP_OK) {
        return ESP_ERR_INVALID_STATE;
    }

    return play_buffered_audio(state, state->bytes_received);
}

static bool run_stream(
    nana_audio_stream_state_t *state,
    const nana_audio_message_t *begin)
{
    reset_stream(state);
    state->active = true;
    state->stream_id = begin->stream_id;
    state->streaming = begin->streaming;
    state->total_bytes = begin->streaming
                             ? NANA_MEDIA_MAX_PLAYBACK_BYTES
                             : begin->total_bytes;
    state->total_samples = begin->total_samples;
    state->total_chunks = begin->streaming
                              ? 0U
                              : (begin->total_bytes +
                                 NANA_SESSION_AUDIO_CHUNK_BYTES - 1U) /
                                    NANA_SESSION_AUDIO_CHUNK_BYTES;

    state->pcm_buffer = heap_caps_malloc(
        state->total_bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (state->pcm_buffer == NULL) {
        ESP_LOGE(TAG,
                 "PSRAM playback buffer unavailable | requested=%" PRIu32
                 "B free_psram=%zu",
                 state->total_bytes,
                 heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
        abort_stream(state, "error", false);
        return false;
    }
    if (emit_ready(state->stream_id) != ESP_OK) {
        abort_stream(state, "error", false);
        return false;
    }

    esp_err_t result = ESP_OK;
    while (state->active) {
        const uint32_t waiting = (uint32_t)uxQueueMessagesWaiting(audio_queue);
        if (waiting > state->queue_high_water) {
            state->queue_high_water = waiting;
        }

        nana_audio_message_t message = {0};
        if (xQueueReceive(
                audio_queue,
                &message,
                pdMS_TO_TICKS(NANA_AUDIO_CHUNK_TIMEOUT_MS)) != pdTRUE) {
            abort_stream(state, "underrun", true);
            return false;
        }

        if (message.type == NANA_AUDIO_MESSAGE_SHUTDOWN) {
            abort_stream(state, "aborted", false);
            return true;
        }
        if (message.type == NANA_AUDIO_MESSAGE_ABORT) {
            abort_stream(state, "aborted", false);
            return false;
        }
        if (message.stream_id != state->stream_id) {
            abort_stream(state, "error", false);
            return false;
        }
        if (message.type == NANA_AUDIO_MESSAGE_CHUNK) {
            if (message.sequence != state->expected_sequence ||
                message.payload_bytes == 0U ||
                (message.payload_bytes % sizeof(int16_t)) != 0U ||
                state->bytes_received + message.payload_bytes >
                    state->total_bytes) {
                abort_stream(state, "error", false);
                return false;
            }
            memcpy(
                state->pcm_buffer + state->bytes_received,
                message.payload,
                message.payload_bytes);
            state->bytes_received += message.payload_bytes;
            ++state->expected_sequence;

            // Credits are control-plane flow control.  Publish them before
            // the potentially blocking I2S write so the session can keep
            // filling the bounded PSRAM/audio queue while DMA drains.
            if (emit_credit(state->stream_id, state->bytes_received) != ESP_OK) {
                abort_stream(state, "error", false);
                return false;
            }

            const bool was_playing = state->playback_started;
            if (was_playing) {
                result = play_buffered_audio(state, state->bytes_received);
                if (result != ESP_OK) {
                    ESP_LOGE(TAG,
                             "Speaker write failed: %s",
                             esp_err_to_name(result));
                    abort_stream(state, "error", false);
                    return false;
                }
            } else if (state->streaming) {
                // Progressive playback starts after the bounded prebuffer.
                // Non-streaming playback deliberately waits for END below so
                // its A/B path is genuinely fully buffered in PSRAM.
                result = start_playback_if_ready(state, begin, false);
                if (result != ESP_OK) {
                    ESP_LOGE(TAG,
                             "Speaker begin/write failed: %s",
                             esp_err_to_name(result));
                    abort_stream(state, "error", false);
                    return false;
                }
            }
            // Keep the WebSocket and Wi-Fi tasks responsive while the DMA
            // ring absorbs the PCM.  A short yield prevents long streams
            // from starving the session heartbeat.
            vTaskDelay(pdMS_TO_TICKS(1U));
            continue;
        }
        if (message.type != NANA_AUDIO_MESSAGE_END ||
            message.total_bytes == 0U ||
            message.total_bytes > NANA_MEDIA_MAX_PLAYBACK_BYTES ||
            (message.total_bytes % sizeof(int16_t)) != 0U ||
            message.chunks == 0U ||
            state->expected_sequence != message.chunks ||
            state->bytes_received != message.total_bytes ||
            (!state->streaming &&
             (message.total_bytes != state->total_bytes ||
              message.chunks != state->total_chunks))) {
            abort_stream(state, "error", false);
            return false;
        }

        state->total_bytes = message.total_bytes;
        state->total_samples = message.total_bytes / sizeof(int16_t);
        state->total_chunks = message.chunks;

        result = start_playback_if_ready(state, begin, true);
        if (result != ESP_OK) {
            ESP_LOGE(TAG,
                     "Speaker begin/write failed: %s",
                     esp_err_to_name(result));
            abort_stream(state, "error", false);
            return false;
        }

        if (!state->playback_started ||
            state->bytes_played != state->total_bytes) {
            abort_stream(state, "error", false);
            return false;
        }

        result = nana_speaker_stream_end(state->speaker);
        state->speaker = NULL;
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "Speaker drain failed: %s", esp_err_to_name(result));
            abort_stream(state, "error", false);
            return false;
        }
        ESP_LOGI(TAG,
                 "NANA_PLAYBACK_DRAINED stream=%" PRIu32
                 " status=complete bytes=%" PRIu32 " queue_high_water=%" PRIu32,
                 state->stream_id,
                 state->bytes_played,
                 state->queue_high_water);
        if (emit_drained(state, "complete") != ESP_OK) {
            ESP_LOGE(TAG, "Could not queue completed playback_drained");
        }
        reset_stream(state);
        return false;
    }
    return false;
}

static void audio_worker(void *argument)
{
    (void)argument;
    nana_audio_stream_state_t state = {0};
    while (true) {
        nana_audio_message_t message = {0};
        if (xQueueReceive(audio_queue, &message, portMAX_DELAY) != pdTRUE) {
            continue;
        }
        if (message.type == NANA_AUDIO_MESSAGE_SHUTDOWN) {
            break;
        }
        if (message.type == NANA_AUDIO_MESSAGE_BEGIN) {
            if (state.active) {
                abort_stream(&state, "aborted", false);
            }
            if (run_stream(&state, &message)) {
                break;
            }
            continue;
        }
        if (message.type == NANA_AUDIO_MESSAGE_ABORT) {
            abort_stream(&state, "aborted", false);
        }
    }
    abort_stream(&state, "aborted", false);
    nana_speaker_mute();
    audio_task = NULL;
    xSemaphoreGive(audio_stopped);
    vTaskDelete(NULL);
}

static esp_err_t enqueue_message(const nana_audio_message_t *message)
{
    if (audio_queue == NULL || message == NULL) {
        return ESP_ERR_INVALID_STATE;
    }
    return xQueueSend(audio_queue, message, 0) == pdTRUE
               ? ESP_OK
               : ESP_ERR_TIMEOUT;
}

esp_err_t nana_session_audio_init(
    nana_session_audio_emit_fn emit,
    nana_session_audio_credit_fn emit_credit,
    void *context)
{
    if (emit == NULL || emit_credit == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    emit_message = emit;
    emit_credit_message = emit_credit;
    emit_context = context;
    if (audio_queue != NULL && audio_task != NULL) {
        return nana_speaker_mute();
    }

    audio_queue = xQueueCreateStatic(
        NANA_AUDIO_QUEUE_DEPTH,
        sizeof(nana_audio_message_t),
        audio_queue_storage,
        &audio_queue_control);
    if (audio_queue == NULL) {
        emit_message = NULL;
        emit_credit_message = NULL;
        emit_context = NULL;
        return ESP_ERR_NO_MEM;
    }
    audio_stopped = xSemaphoreCreateBinaryStatic(&audio_stopped_control);
    if (audio_stopped == NULL) {
        vQueueDelete(audio_queue);
        audio_queue = NULL;
        emit_message = NULL;
        emit_credit_message = NULL;
        emit_context = NULL;
        return ESP_ERR_NO_MEM;
    }
    while (xSemaphoreTake(audio_stopped, 0) == pdTRUE) {
    }
    audio_task = xTaskCreateStatic(
            audio_worker,
            "nana_audio_rx",
            sizeof(audio_task_stack) / sizeof(audio_task_stack[0]),
            NULL,
            NANA_AUDIO_TASK_PRIORITY,
            audio_task_stack,
            &audio_task_control);
    if (audio_task == NULL) {
        vSemaphoreDelete(audio_stopped);
        audio_stopped = NULL;
        vQueueDelete(audio_queue);
        audio_queue = NULL;
        emit_message = NULL;
        emit_context = NULL;
        return ESP_ERR_NO_MEM;
    }
    return nana_speaker_mute();
}

esp_err_t nana_session_audio_deinit(uint32_t timeout_ms)
{
    if (timeout_ms == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    nana_speaker_mute();
    const bool task_running = audio_task != NULL;
    if (task_running) {
        const nana_audio_message_t shutdown = {
            .type = NANA_AUDIO_MESSAGE_SHUTDOWN,
        };
        if (xQueueSend(
                audio_queue,
                &shutdown,
                pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
            return ESP_ERR_TIMEOUT;
        }
    }
    if (task_running &&
        xSemaphoreTake(
            audio_stopped,
            pdMS_TO_TICKS(timeout_ms)) != pdTRUE) {
        return ESP_ERR_TIMEOUT;
    }
    if (audio_queue != NULL) {
        vQueueDelete(audio_queue);
        audio_queue = NULL;
    }
    if (audio_stopped != NULL) {
        vSemaphoreDelete(audio_stopped);
        audio_stopped = NULL;
    }
    audio_task = NULL;
    emit_message = NULL;
    emit_credit_message = NULL;
    emit_context = NULL;
    return nana_speaker_mute();
}

esp_err_t nana_session_audio_begin(
    uint32_t stream_id,
    uint32_t sample_rate,
    uint32_t channels,
    uint32_t sample_width,
    uint32_t total_bytes,
    uint32_t total_samples,
    uint32_t chunk_bytes,
    bool streaming)
{
    if (stream_id == 0U || sample_rate != 16000U || channels != 1U ||
        sample_width != 2U || total_bytes > NANA_MEDIA_MAX_PLAYBACK_BYTES ||
        chunk_bytes != NANA_SESSION_AUDIO_CHUNK_BYTES ||
        (streaming
             ? (total_bytes != 0U || total_samples != 0U)
             : (total_bytes == 0U ||
                (total_bytes % sample_width) != 0U ||
                total_samples != total_bytes / sample_width))) {
        return ESP_ERR_INVALID_ARG;
    }
    nana_audio_message_t message = {
        .type = NANA_AUDIO_MESSAGE_BEGIN,
        .stream_id = stream_id,
        .sample_rate = sample_rate,
        .channels = channels,
        .sample_width = sample_width,
        .total_bytes = total_bytes,
        .total_samples = total_samples,
        .chunk_bytes = chunk_bytes,
        .streaming = streaming,
    };
    return enqueue_message(&message);
}

esp_err_t nana_session_audio_enqueue_frame(
    const uint8_t *frame,
    size_t frame_bytes)
{
    if (frame == NULL || frame_bytes <= NANA_MEDIA_HEADER_BYTES ||
        frame_bytes > NANA_MEDIA_HEADER_BYTES + NANA_SESSION_AUDIO_CHUNK_BYTES ||
        memcmp(frame, NANA_MEDIA_MAGIC, 4U) != 0 ||
        frame[4] != NANA_MEDIA_VERSION ||
        frame[5] != NANA_MEDIA_KIND_PCM_DOWNLINK) {
        return ESP_ERR_INVALID_ARG;
    }
    const uint32_t payload_bytes = read_be32(frame + 16U);
    if (payload_bytes == 0U ||
        payload_bytes > NANA_SESSION_AUDIO_CHUNK_BYTES ||
        frame_bytes != NANA_MEDIA_HEADER_BYTES + payload_bytes) {
        return ESP_ERR_INVALID_SIZE;
    }
    const uint8_t *payload = frame + NANA_MEDIA_HEADER_BYTES;
    const uint32_t expected_crc = read_be32(frame + 20U);
    if (esp_rom_crc32_le(0U, payload, payload_bytes) != expected_crc) {
        return ESP_ERR_INVALID_CRC;
    }

    nana_audio_message_t message = {
        .type = NANA_AUDIO_MESSAGE_CHUNK,
        .stream_id = read_be32(frame + 8U),
        .sequence = read_be32(frame + 12U),
        .payload_bytes = (uint16_t)payload_bytes,
    };
    memcpy(message.payload, payload, payload_bytes);
    return enqueue_message(&message);
}

esp_err_t nana_session_audio_end(
    uint32_t stream_id,
    uint32_t total_bytes,
    uint32_t chunks)
{
    if (stream_id == 0U || total_bytes == 0U || chunks == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    nana_audio_message_t message = {
        .type = NANA_AUDIO_MESSAGE_END,
        .stream_id = stream_id,
        .total_bytes = total_bytes,
        .chunks = chunks,
    };
    return enqueue_message(&message);
}

esp_err_t nana_session_audio_abort(uint32_t stream_id, const char *reason)
{
    nana_audio_message_t message = {
        .type = NANA_AUDIO_MESSAGE_ABORT,
        .stream_id = stream_id,
    };
    strlcpy(
        message.reason,
        reason != NULL ? reason : "core_abort",
        sizeof(message.reason));
    return enqueue_message(&message);
}

void nana_session_audio_connection_closed(void)
{
    if (nana_session_audio_abort(0U, "connection_closed") != ESP_OK) {
        ESP_LOGE(TAG, "Could not queue disconnect abort; forcing amplifier mute");
        nana_speaker_mute();
    }
}
