#include <inttypes.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "cJSON.h"
#include "driver/uart.h"
#include "esp_app_desc.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_random.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_websocket_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/queue.h"
#include "freertos/task.h"
#include "mbedtls/base64.h"
#include "nvs.h"

#include "nana_presence_session.h"
#include "nana_camera.h"
#include "nana_display.h"
#include "nana_session_audio.h"
#include "nana_session_camera.h"
#include "nana_session_uplink.h"
#include "nana_speaker.h"
#include "nana_wifi.h"

#define NANA_SESSION_PROTOCOL "nana.presence.session.v1"
#define NANA_SESSION_PROFILE "presence_session"
#define NANA_SESSION_NAMESPACE "nana_session"
#define NANA_SESSION_URI_KEY "uri"
#define NANA_SESSION_TOKEN_KEY "token"

#define NANA_SESSION_URI_BYTES 256U
#define NANA_SESSION_TOKEN_BYTES 129U
#define NANA_SESSION_HEADERS_BYTES (NANA_SESSION_TOKEN_BYTES + 32U)
#define NANA_SESSION_RX_BYTES 1050U
#define NANA_SESSION_UART_LINE_BYTES 640U
#define NANA_SESSION_UART_BUFFER_BYTES 1024U
#define NANA_SESSION_JSON_BYTES 768U
#define NANA_SESSION_TX_QUEUE_DEPTH 24U
#define NANA_SESSION_TX_PAYLOAD_BYTES NANA_SESSION_RX_BYTES
#define NANA_SESSION_CAPTURE_MAX_BYTES (16000U * 20U * sizeof(int16_t))
#define NANA_SESSION_CAPTURE_MAX_CHUNKS                              \
    ((NANA_SESSION_CAPTURE_MAX_BYTES + NANA_SESSION_AUDIO_CHUNK_BYTES - 1U) / \
     NANA_SESSION_AUDIO_CHUNK_BYTES)
#define NANA_SESSION_CAMERA_MAX_BYTES NANA_CAMERA_MAX_JPEG_BYTES
#define NANA_SESSION_CAMERA_MAX_CHUNKS                               \
    ((NANA_SESSION_CAMERA_MAX_BYTES + NANA_SESSION_AUDIO_CHUNK_BYTES - 1U) / \
     NANA_SESSION_AUDIO_CHUNK_BYTES)

#define NANA_SESSION_CONNECT_TIMEOUT_MS 5000U
#define NANA_SESSION_HELLO_TIMEOUT_MS 5000U
#define NANA_SESSION_ACK_TIMEOUT_MS 15000U
#define NANA_SESSION_HEARTBEAT_DEFAULT_MS 5000U
#define NANA_SESSION_HEARTBEAT_MIN_MS 1000U
#define NANA_SESSION_HEARTBEAT_MAX_MS 60000U
#define NANA_SESSION_BACKOFF_MAX_MS 5000U
#define NANA_SESSION_BACKOFF_JITTER_MS 250U
#define NANA_SESSION_SEND_TIMEOUT_MS 3000U
#define NANA_SESSION_WIFI_TEST_DROP_MS 3000U
#define NANA_SESSION_WORKER_STOP_TIMEOUT_MS 5000U
#define NANA_SESSION_MEDIA_HEADER_BYTES 24U
#define NANA_SESSION_AUDIO_CREDIT_BATCH_MAX 16U

_Static_assert(
    NANA_SESSION_TX_PAYLOAD_BYTES >=
        NANA_SESSION_MEDIA_HEADER_BYTES + NANA_SESSION_AUDIO_CHUNK_BYTES,
    "session TX queue must hold one complete bounded media frame");

#define SESSION_CONNECTED_BIT BIT0
#define SESSION_DISCONNECTED_BIT BIT1
#define SESSION_WELCOME_BIT BIT2
#define SESSION_ACK_BIT BIT3
#define SESSION_PROTOCOL_ERROR_BIT BIT4

typedef struct {
    char uri[NANA_SESSION_URI_BYTES];
    char token[NANA_SESSION_TOKEN_BYTES];
} nana_session_config_t;

typedef struct {
    uint8_t opcode;
    uint16_t length;
    uint8_t payload[NANA_SESSION_TX_PAYLOAD_BYTES];
} nana_session_tx_message_t;

typedef struct {
    EventGroupHandle_t events;
    QueueHandle_t tx_queue;
    uint8_t rx[NANA_SESSION_RX_BYTES];
    size_t rx_used;
    size_t rx_expected;
    uint8_t rx_opcode;
    uint32_t heartbeat_ms;
    uint32_t expected_sequence;
    char session_id[65];
    volatile bool session_ready;
    portMUX_TYPE credit_lock;
    uint32_t pending_credit_stream_id;
    uint32_t pending_credit_count;
    uint32_t pending_credit_bytes;
} nana_session_context_t;

typedef struct {
    bool audio_started;
    bool uplink_started;
    bool camera_started;
} nana_session_supervisor_t;

static const char *TAG = "nana_session";
static bool serial_listener_started = false;

static esp_err_t record_cleanup_result(
    esp_err_t current,
    esp_err_t candidate,
    const char *component)
{
    if (candidate != ESP_OK) {
        ESP_LOGE(
            TAG,
            "NANA_SESSION_SUPERVISOR cleanup=%s status=failed error=%s",
            component,
            esp_err_to_name(candidate));
        if (current == ESP_OK) {
            return candidate;
        }
    }
    return current;
}

static esp_err_t cleanup_session_supervisor(
    nana_session_context_t *context,
    const nana_session_supervisor_t *supervisor)
{
    esp_err_t result = nana_speaker_mute();
    if (context != NULL) {
        context->session_ready = false;
    }
    if (supervisor != NULL && supervisor->camera_started) {
        result = record_cleanup_result(
            result,
            nana_session_camera_deinit(NANA_SESSION_WORKER_STOP_TIMEOUT_MS),
            "camera");
    }
    if (supervisor != NULL && supervisor->uplink_started) {
        result = record_cleanup_result(
            result,
            nana_session_uplink_deinit(NANA_SESSION_WORKER_STOP_TIMEOUT_MS),
            "uplink");
    }
    if (supervisor != NULL && supervisor->audio_started) {
        result = record_cleanup_result(
            result,
            nana_session_audio_deinit(NANA_SESSION_WORKER_STOP_TIMEOUT_MS),
            "audio");
    }
    if (context != NULL && context->tx_queue != NULL) {
        vQueueDelete(context->tx_queue);
        context->tx_queue = NULL;
    }
    if (context != NULL && context->events != NULL) {
        vEventGroupDelete(context->events);
        context->events = NULL;
    }
    result = record_cleanup_result(
        result, nana_speaker_mute(), "speaker_hard_mute");
    ESP_LOGI(
        TAG,
        "NANA_SESSION_SUPERVISOR state=stopped cleanup=%s",
        result == ESP_OK ? "complete" : "degraded");
    return result;
}

static esp_err_t fail_session_start(
    esp_err_t cause,
    nana_session_context_t *context,
    const nana_session_supervisor_t *supervisor)
{
    ESP_LOGE(
        TAG,
        "NANA_SESSION_SUPERVISOR state=rollback cause=%s",
        esp_err_to_name(cause));
    const esp_err_t cleanup_result =
        cleanup_session_supervisor(context, supervisor);
    return cause != ESP_OK ? cause : cleanup_result;
}

static esp_err_t initialize_serial_input(void)
{
    const esp_err_t result = uart_driver_install(
        UART_NUM_0,
        NANA_SESSION_UART_BUFFER_BYTES,
        0,
        0,
        NULL,
        0);
    if (result == ESP_OK || result == ESP_ERR_INVALID_STATE) {
        return ESP_OK;
    }
    return result;
}

static bool read_serial_line(char *line, size_t capacity, TickType_t timeout)
{
    if (line == NULL || capacity < 2U) {
        return false;
    }

    const bool wait_forever = timeout == portMAX_DELAY;
    const TickType_t started_at = xTaskGetTickCount();
    size_t length = 0;

    while (wait_forever || (xTaskGetTickCount() - started_at) < timeout) {
        uint8_t byte = 0;
        const int bytes_read = uart_read_bytes(
            UART_NUM_0, &byte, 1, pdMS_TO_TICKS(100));
        if (bytes_read != 1) {
            continue;
        }
        if (byte == '\r') {
            continue;
        }
        if (byte == '\n') {
            if (length == 0U) {
                continue;
            }
            line[length] = '\0';
            return true;
        }
        if (length + 1U >= capacity) {
            length = 0;
            continue;
        }
        if (byte >= 32U && byte < 127U) {
            line[length++] = (char)byte;
        }
    }
    return false;
}

static bool decode_serial_field(
    const char *encoded, char *output, size_t capacity)
{
    if (encoded == NULL || output == NULL || capacity < 2U) {
        return false;
    }
    size_t output_length = 0;
    const int result = mbedtls_base64_decode(
        (unsigned char *)output,
        capacity - 1U,
        &output_length,
        (const unsigned char *)encoded,
        strlen(encoded));
    if (result != 0 || output_length == 0U || output_length >= capacity) {
        return false;
    }
    output[output_length] = '\0';
    return strlen(output) == output_length;
}

static bool valid_session_uri(const char *uri)
{
    if (uri == NULL || strncmp(uri, "ws://", 5U) != 0) {
        return false;
    }
    const char *authority = uri + 5U;
    const char *path = strchr(authority, '/');
    return path != NULL && path > authority &&
           strcmp(path, "/presence/v1") == 0;
}

static bool parse_set_command(char *line, nana_session_config_t *config)
{
    static const char prefix[] = "NANA_SESSION_SET ";
    if (line == NULL || config == NULL ||
        strncmp(line, prefix, sizeof(prefix) - 1U) != 0) {
        return false;
    }

    char *save_pointer = NULL;
    char *uri_encoded = strtok_r(
        line + sizeof(prefix) - 1U, " ", &save_pointer);
    char *token_encoded = strtok_r(NULL, " ", &save_pointer);
    if (uri_encoded == NULL || token_encoded == NULL ||
        strtok_r(NULL, " ", &save_pointer) != NULL) {
        return false;
    }

    nana_session_config_t parsed = {0};
    if (!decode_serial_field(
            uri_encoded, parsed.uri, sizeof(parsed.uri)) ||
        !decode_serial_field(
            token_encoded, parsed.token, sizeof(parsed.token)) ||
        !valid_session_uri(parsed.uri)) {
        return false;
    }
    *config = parsed;
    return true;
}

static esp_err_t load_config(nana_session_config_t *config)
{
    if (config == NULL) {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(
        NANA_SESSION_NAMESPACE, NVS_READONLY, &handle);
    if (result != ESP_OK) {
        return result;
    }

    size_t uri_length = sizeof(config->uri);
    size_t token_length = sizeof(config->token);
    result = nvs_get_str(
        handle, NANA_SESSION_URI_KEY, config->uri, &uri_length);
    if (result == ESP_OK) {
        result = nvs_get_str(
            handle,
            NANA_SESSION_TOKEN_KEY,
            config->token,
            &token_length);
    }
    nvs_close(handle);
    if (result == ESP_OK &&
        (!valid_session_uri(config->uri) || config->token[0] == '\0')) {
        return ESP_ERR_INVALID_STATE;
    }
    return result;
}

static esp_err_t save_config(const nana_session_config_t *config)
{
    if (config == NULL || !valid_session_uri(config->uri) ||
        config->token[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(
        NANA_SESSION_NAMESPACE, NVS_READWRITE, &handle);
    if (result != ESP_OK) {
        return result;
    }
    result = nvs_set_str(handle, NANA_SESSION_URI_KEY, config->uri);
    if (result == ESP_OK) {
        result = nvs_set_str(
            handle, NANA_SESSION_TOKEN_KEY, config->token);
    }
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}

static esp_err_t clear_config(void)
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(
        NANA_SESSION_NAMESPACE, NVS_READWRITE, &handle);
    if (result != ESP_OK) {
        return result;
    }
    result = nvs_erase_all(handle);
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}

static esp_err_t wait_for_initial_config(nana_session_config_t *config)
{
    char line[NANA_SESSION_UART_LINE_BYTES] = {0};
    while (true) {
        printf("NANA_SESSION_PROVISION_READY protocol=base64-v1\n");
        fflush(stdout);
        if (!read_serial_line(
                line, sizeof(line), pdMS_TO_TICKS(5000))) {
            continue;
        }
        if (!parse_set_command(line, config)) {
            ESP_LOGW(TAG, "Ignored invalid session provisioning command");
            continue;
        }
        const esp_err_t result = save_config(config);
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "Session config save failed: %s",
                     esp_err_to_name(result));
            continue;
        }
        printf("NANA_SESSION_SAVED uri=%s token=hidden\n", config->uri);
        fflush(stdout);
        return ESP_OK;
    }
}

static void serial_provisioning_task(void *argument)
{
    (void)argument;
    char line[NANA_SESSION_UART_LINE_BYTES] = {0};

    while (true) {
        if (!read_serial_line(line, sizeof(line), portMAX_DELAY)) {
            continue;
        }
        if (strcmp(line, "NANA_SESSION_CLEAR") == 0) {
            const esp_err_t result = clear_config();
            printf("NANA_SESSION_CLEARED status=%s\n",
                   esp_err_to_name(result));
            fflush(stdout);
            if (result == ESP_OK) {
                vTaskDelay(pdMS_TO_TICKS(100));
                esp_restart();
            }
            continue;
        }

        if (strcmp(line, "NANA_SESSION_TEST_WIFI_DROP") == 0) {
            printf("NANA_SESSION_WIFI_DROP_BEGIN duration=%ums\n",
                   (unsigned)NANA_SESSION_WIFI_TEST_DROP_MS);
            fflush(stdout);
            const esp_err_t result = nana_wifi_interrupt_for_test(
                NANA_SESSION_WIFI_TEST_DROP_MS);
            printf("NANA_SESSION_WIFI_DROP_DONE duration=%ums status=%s\n",
                   (unsigned)NANA_SESSION_WIFI_TEST_DROP_MS,
                   esp_err_to_name(result));
            fflush(stdout);
            continue;
        }

        nana_session_config_t config = {0};
        if (!parse_set_command(line, &config)) {
            continue;
        }
        nana_session_config_t current = {0};
        if (load_config(&current) == ESP_OK &&
            strcmp(current.uri, config.uri) == 0 &&
            strcmp(current.token, config.token) == 0) {
            printf("NANA_SESSION_UNCHANGED uri=%s token=hidden\n",
                   config.uri);
            fflush(stdout);
            continue;
        }

        const esp_err_t result = save_config(&config);
        printf("NANA_SESSION_SAVED uri=%s token=hidden status=%s\n",
               config.uri,
               esp_err_to_name(result));
        fflush(stdout);
        if (result == ESP_OK) {
            vTaskDelay(pdMS_TO_TICKS(100));
            esp_restart();
        }
    }
}

static esp_err_t start_serial_listener(void)
{
    if (serial_listener_started) {
        return ESP_OK;
    }
    if (xTaskCreate(
            serial_provisioning_task,
            "nana_session_cfg",
            4096,
            NULL,
            5,
            NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    serial_listener_started = true;
    return ESP_OK;
}

static void reset_rx(nana_session_context_t *context)
{
    context->rx_used = 0U;
    context->rx_expected = 0U;
    context->rx_opcode = 0U;
    context->rx[0] = '\0';
}

static esp_err_t queue_session_payload(
    uint8_t opcode,
    const uint8_t *payload,
    size_t payload_bytes,
    void *argument)
{
    nana_session_context_t *context = (nana_session_context_t *)argument;
    if (context == NULL || context->tx_queue == NULL ||
        !context->session_ready || payload == NULL || payload_bytes == 0U ||
        payload_bytes > NANA_SESSION_TX_PAYLOAD_BYTES ||
        (opcode != 0x01U && opcode != 0x02U)) {
        return ESP_ERR_INVALID_ARG;
    }
    nana_session_tx_message_t queued = {0};
    queued.opcode = opcode;
    queued.length = (uint16_t)payload_bytes;
    memcpy(queued.payload, payload, payload_bytes);
    return xQueueSend(
               context->tx_queue,
               &queued,
               pdMS_TO_TICKS(1000U)) == pdTRUE
               ? ESP_OK
               : ESP_ERR_TIMEOUT;
}

static esp_err_t queue_session_text(const char *message, void *argument)
{
    if (message == NULL || message[0] == '\0') {
        return ESP_ERR_INVALID_ARG;
    }
    return queue_session_payload(
        0x01U,
        (const uint8_t *)message,
        strlen(message),
        argument);
}

static esp_err_t queue_session_binary(
    const uint8_t *message,
    size_t message_bytes,
    void *argument)
{
    return queue_session_payload(
        0x02U, message, message_bytes, argument);
}

static esp_err_t queue_session_audio_credit(
    uint32_t stream_id,
    uint32_t bytes_received,
    void *argument)
{
    nana_session_context_t *context =
        (nana_session_context_t *)argument;
    if (context == NULL || stream_id == 0U || !context->session_ready) {
        return ESP_ERR_INVALID_STATE;
    }

    // Do not make the audio worker wait on the WebSocket TX queue.  The
    // supervisor drains this compact counter on its own task and batches up
    // to the protocol credit limit into one control frame.
    portENTER_CRITICAL(&context->credit_lock);
    if (context->pending_credit_count == 0U ||
        context->pending_credit_stream_id == stream_id) {
        context->pending_credit_stream_id = stream_id;
        ++context->pending_credit_count;
        context->pending_credit_bytes = bytes_received;
    } else {
        // Playback streams are serialized.  A new stream supersedes stale
        // credits left over from a disconnected stream.
        context->pending_credit_stream_id = stream_id;
        context->pending_credit_count = 1U;
        context->pending_credit_bytes = bytes_received;
    }
    portEXIT_CRITICAL(&context->credit_lock);
    return ESP_OK;
}

static void reset_session_audio_credits(nana_session_context_t *context)
{
    if (context == NULL) {
        return;
    }
    portENTER_CRITICAL(&context->credit_lock);
    context->pending_credit_stream_id = 0U;
    context->pending_credit_count = 0U;
    context->pending_credit_bytes = 0U;
    portEXIT_CRITICAL(&context->credit_lock);
}

static void mark_protocol_error(
    nana_session_context_t *context, const char *reason)
{
    ESP_LOGE(TAG, "Session protocol error: %s", reason);
    reset_rx(context);
    xEventGroupSetBits(context->events, SESSION_PROTOCOL_ERROR_BIT);
}

static bool json_uint32(
    const cJSON *root,
    const char *name,
    uint32_t minimum,
    uint32_t maximum,
    uint32_t *output)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsNumber(item) || item->valuedouble < (double)minimum ||
        item->valuedouble > (double)maximum) {
        return false;
    }
    const uint32_t value = (uint32_t)item->valuedouble;
    if ((double)value != item->valuedouble) {
        return false;
    }
    *output = value;
    return true;
}

static bool json_optional_bool(
    const cJSON *root,
    const char *name,
    bool default_value,
    bool *output)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    if (item == NULL) {
        *output = default_value;
        return true;
    }
    if (!cJSON_IsBool(item)) {
        return false;
    }
    *output = cJSON_IsTrue(item);
    return true;
}

static bool json_text_equals(
    const cJSON *root,
    const char *name,
    const char *expected)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    return cJSON_IsString(item) &&
           strcmp(item->valuestring, expected) == 0;
}

static bool json_bounded_text(
    const cJSON *root,
    const char *name,
    size_t maximum_bytes,
    const char **output)
{
    const cJSON *item = cJSON_GetObjectItemCaseSensitive(root, name);
    if (!cJSON_IsString(item) || item->valuestring[0] == '\0' ||
        strlen(item->valuestring) > maximum_bytes) {
        return false;
    }
    *output = item->valuestring;
    return true;
}

static esp_err_t queue_display_ack(
    nana_session_context_t *context,
    uint32_t request_id,
    const char *kind,
    const char *tag,
    bool accepted,
    const char *reason)
{
    char message[320] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"display_ack\",\"protocol\":\"%s\","
        "\"request_id\":%" PRIu32 ",\"kind\":\"%s\","
        "\"tag\":\"%s\",\"status\":\"%s\",\"reason\":\"%s\"}",
        NANA_SESSION_PROTOCOL,
        request_id,
        kind,
        tag,
        accepted ? "accepted" : "rejected",
        reason);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return queue_session_text(message, context);
}

static void process_text_message(
    nana_session_context_t *context, const char *message)
{
    cJSON *root = cJSON_Parse(message);
    if (root == NULL) {
        mark_protocol_error(context, "invalid JSON from Core");
        return;
    }

    const cJSON *type = cJSON_GetObjectItemCaseSensitive(root, "type");
    const cJSON *protocol = cJSON_GetObjectItemCaseSensitive(
        root, "protocol");
    if (!cJSON_IsString(type) || !cJSON_IsString(protocol) ||
        strcmp(protocol->valuestring, NANA_SESSION_PROTOCOL) != 0) {
        cJSON_Delete(root);
        mark_protocol_error(context, "missing type or protocol mismatch");
        return;
    }

    if (strcmp(type->valuestring, "welcome") == 0) {
        const cJSON *session_id = cJSON_GetObjectItemCaseSensitive(
            root, "session_id");
        const cJSON *heartbeat = cJSON_GetObjectItemCaseSensitive(
            root, "heartbeat_ms");
        if (!cJSON_IsString(session_id) || session_id->valuestring[0] == '\0' ||
            strlen(session_id->valuestring) >= sizeof(context->session_id) ||
            !cJSON_IsNumber(heartbeat) || heartbeat->valuedouble < 0.0) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid welcome frame");
            return;
        }
        uint32_t heartbeat_ms = (uint32_t)heartbeat->valuedouble;
        if (heartbeat_ms < NANA_SESSION_HEARTBEAT_MIN_MS) {
            heartbeat_ms = NANA_SESSION_HEARTBEAT_MIN_MS;
        } else if (heartbeat_ms > NANA_SESSION_HEARTBEAT_MAX_MS) {
            heartbeat_ms = NANA_SESSION_HEARTBEAT_MAX_MS;
        }
        strlcpy(
            context->session_id,
            session_id->valuestring,
            sizeof(context->session_id));
        context->heartbeat_ms = heartbeat_ms;
        xEventGroupSetBits(context->events, SESSION_WELCOME_BIT);
        cJSON_Delete(root);
        return;
    }

    if (strcmp(type->valuestring, "heartbeat_ack") == 0) {
        const cJSON *sequence = cJSON_GetObjectItemCaseSensitive(
            root, "sequence");
        if (!cJSON_IsNumber(sequence) || sequence->valuedouble < 0.0 ||
            (uint32_t)sequence->valuedouble != context->expected_sequence) {
            cJSON_Delete(root);
            mark_protocol_error(context, "heartbeat sequence mismatch");
            return;
        }
        xEventGroupSetBits(context->events, SESSION_ACK_BIT);
        cJSON_Delete(root);
        return;
    }

    if (strcmp(type->valuestring, "display_state") == 0 ||
        strcmp(type->valuestring, "display_event") == 0) {
        uint32_t request_id = 0U;
        const char *tag = NULL;
        const bool is_event =
            strcmp(type->valuestring, "display_event") == 0;
        const char *kind = is_event ? "event" : "state";
        if (!json_uint32(
                root, "request_id", 1U, UINT32_MAX, &request_id) ||
            !json_bounded_text(root, "tag", 32U, &tag)) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid display command frame");
            return;
        }

        bool accepted = false;
        const char *reason = "not_applied";
        nana_face_state_t face_state = NANA_FACE_IDLE;
        if (!nana_display_is_running()) {
            reason = "display_unavailable";
        } else if (!nana_face_state_from_tag(tag, &face_state)) {
            reason = "unknown_tag";
        } else if (!is_event &&
                   nana_face_state_tag_kind(face_state) !=
                       NANA_FACE_TAG_EXPRESSION) {
            reason = "gesture_requires_event";
        } else if (is_event && face_state == NANA_FACE_IDLE) {
            reason = "neutral_event_forbidden";
        } else {
            accepted = is_event
                           ? nana_display_trigger_event(tag)
                           : nana_display_set_expression(tag);
            reason = accepted ? "accepted" : "queue_full";
        }

        const esp_err_t ack_result = queue_display_ack(
            context,
            request_id,
            kind,
            tag,
            accepted,
            reason);
        cJSON_Delete(root);
        if (ack_result != ESP_OK) {
            mark_protocol_error(context, "display acknowledgement failed");
        }
        return;
    }

    if (strcmp(type->valuestring, "camera_snapshot") == 0) {
        uint32_t request_id = 0U;
        uint32_t max_bytes = 0U;
        if (!json_text_equals(root, "codec", "jpeg") ||
            !json_text_equals(root, "integrity", "crc32_per_chunk") ||
            !json_uint32(
                root, "request_id", 1U, UINT32_MAX, &request_id) ||
            !json_uint32(
                root,
                "max_bytes",
                4096U,
                NANA_SESSION_CAMERA_MAX_BYTES,
                &max_bytes)) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid camera_snapshot frame");
            return;
        }
        const esp_err_t result = nana_session_camera_request(
            request_id, max_bytes);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "camera_snapshot was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "camera_cancel") == 0) {
        uint32_t stream_id = 0U;
        const char *reason = NULL;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_bounded_text(root, "reason", 96U, &reason)) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid camera_cancel frame");
            return;
        }
        const esp_err_t result = nana_session_camera_cancel(stream_id);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "camera_cancel was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "camera_ready") == 0) {
        uint32_t stream_id = 0U;
        uint32_t credits = 0U;
        uint32_t queue_capacity = 0U;
        uint32_t max_bytes = 0U;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(root, "credits", 1U, 16U, &credits) ||
            !json_uint32(
                root, "queue_capacity", 1U, 16U, &queue_capacity) ||
            !json_uint32(
                root,
                "max_bytes",
                4096U,
                NANA_SESSION_CAMERA_MAX_BYTES,
                &max_bytes) ||
            credits > queue_capacity) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid camera_ready frame");
            return;
        }
        const esp_err_t result = nana_session_camera_ready(
            stream_id, credits, max_bytes);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "camera_ready was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "camera_credit") == 0) {
        uint32_t stream_id = 0U;
        uint32_t credits = 0U;
        uint32_t bytes_received = 0U;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(root, "credits", 1U, 16U, &credits) ||
            !json_uint32(
                root,
                "bytes_received",
                1U,
                NANA_SESSION_CAMERA_MAX_BYTES,
                &bytes_received)) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid camera_credit frame");
            return;
        }
        const esp_err_t result = nana_session_camera_credit(
            stream_id, credits, bytes_received);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "camera_credit was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "camera_received") == 0) {
        uint32_t stream_id = 0U;
        uint32_t total_bytes = 0U;
        uint32_t chunks = 0U;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(
                root,
                "total_bytes",
                1U,
                NANA_SESSION_CAMERA_MAX_BYTES,
                &total_bytes) ||
            !json_uint32(
                root,
                "chunks",
                1U,
                NANA_SESSION_CAMERA_MAX_CHUNKS,
                &chunks)) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid camera_received frame");
            return;
        }
        const esp_err_t result = nana_session_camera_received(
            stream_id, total_bytes, chunks);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "camera_received was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "capture_ready") == 0) {
        uint32_t stream_id = 0U;
        uint32_t credits = 0U;
        uint32_t queue_capacity = 0U;
        uint32_t max_bytes = 0U;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(root, "credits", 1U, 16U, &credits) ||
            !json_uint32(
                root, "queue_capacity", 1U, 16U, &queue_capacity) ||
            !json_uint32(
                root,
                "max_bytes",
                NANA_SESSION_CAPTURE_MAX_BYTES,
                NANA_SESSION_CAPTURE_MAX_BYTES,
                &max_bytes) ||
            credits > queue_capacity) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid capture_ready frame");
            return;
        }
        const esp_err_t result = nana_session_uplink_capture_ready(
            stream_id, credits, max_bytes);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "capture_ready was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "capture_credit") == 0) {
        uint32_t stream_id = 0U;
        uint32_t credits = 0U;
        uint32_t bytes_received = 0U;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(root, "credits", 1U, 16U, &credits) ||
            !json_uint32(
                root,
                "bytes_received",
                1U,
                NANA_SESSION_CAPTURE_MAX_BYTES,
                &bytes_received)) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid capture_credit frame");
            return;
        }
        const esp_err_t result = nana_session_uplink_capture_credit(
            stream_id, credits, bytes_received);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "capture_credit was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "capture_received") == 0) {
        uint32_t stream_id = 0U;
        uint32_t wire_bytes = 0U;
        uint32_t effective_bytes = 0U;
        uint32_t chunks = 0U;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(
                root,
                "wire_bytes",
                2U,
                NANA_SESSION_CAPTURE_MAX_BYTES,
                &wire_bytes) ||
            !json_uint32(
                root,
                "effective_bytes",
                2U,
                NANA_SESSION_CAPTURE_MAX_BYTES,
                &effective_bytes) ||
            !json_uint32(
                root,
                "chunks",
                1U,
                NANA_SESSION_CAPTURE_MAX_CHUNKS,
                &chunks) ||
            effective_bytes > wire_bytes) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid capture_received frame");
            return;
        }
        const esp_err_t result = nana_session_uplink_capture_received(
            stream_id, wire_bytes, effective_bytes, chunks);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "capture_received was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "turn_complete") == 0) {
        uint32_t stream_id = 0U;
        const cJSON *status = cJSON_GetObjectItemCaseSensitive(root, "status");
        const cJSON *reason = cJSON_GetObjectItemCaseSensitive(root, "reason");
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !cJSON_IsString(status) || status->valuestring[0] == '\0' ||
            !cJSON_IsString(reason) || reason->valuestring[0] == '\0') {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid turn_complete frame");
            return;
        }
        const esp_err_t result = nana_session_uplink_turn_complete(
            stream_id, status->valuestring, reason->valuestring);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "turn_complete was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "playback_begin") == 0) {
        uint32_t stream_id = 0U;
        uint32_t sample_rate = 0U;
        uint32_t channels = 0U;
        uint32_t sample_width = 0U;
        uint32_t total_bytes = 0U;
        uint32_t total_samples = 0U;
        uint32_t chunk_bytes = 0U;
        bool streaming = false;
        if (!json_text_equals(root, "codec", "pcm_s16le") ||
            !json_text_equals(root, "integrity", "crc32_per_chunk") ||
            !json_optional_bool(root, "streaming", false, &streaming) ||
            !json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(root, "sample_rate", 16000U, 16000U, &sample_rate) ||
            !json_uint32(root, "channels", 1U, 1U, &channels) ||
            !json_uint32(root, "sample_width", 2U, 2U, &sample_width) ||
            !json_uint32(root, "total_bytes", 0U, 4U * 1024U * 1024U,
                         &total_bytes) ||
            !json_uint32(root, "total_samples", 0U, 2U * 1024U * 1024U,
                         &total_samples) ||
            !json_uint32(root, "chunk_bytes", NANA_SESSION_AUDIO_CHUNK_BYTES,
                         NANA_SESSION_AUDIO_CHUNK_BYTES, &chunk_bytes) ||
            (streaming
                 ? (total_bytes != 0U || total_samples != 0U)
                 : (total_bytes < 2U || total_samples < 1U))) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid playback_begin frame");
            return;
        }
        const esp_err_t result = nana_session_audio_begin(
            stream_id,
            sample_rate,
            channels,
            sample_width,
            total_bytes,
            total_samples,
            chunk_bytes,
            streaming);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "playback_begin was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "playback_end") == 0) {
        uint32_t stream_id = 0U;
        uint32_t total_bytes = 0U;
        uint32_t chunks = 0U;
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            !json_uint32(root, "total_bytes", 2U, 4U * 1024U * 1024U,
                         &total_bytes) ||
            !json_uint32(root, "chunks", 1U, 4096U, &chunks)) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid playback_end frame");
            return;
        }
        const esp_err_t result =
            nana_session_audio_end(stream_id, total_bytes, chunks);
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "playback_end was rejected");
        }
        return;
    }

    if (strcmp(type->valuestring, "playback_abort") == 0) {
        uint32_t stream_id = 0U;
        const cJSON *reason = cJSON_GetObjectItemCaseSensitive(root, "reason");
        if (!json_uint32(root, "stream_id", 1U, UINT32_MAX, &stream_id) ||
            (reason != NULL && !cJSON_IsString(reason))) {
            cJSON_Delete(root);
            mark_protocol_error(context, "invalid playback_abort frame");
            return;
        }
        const esp_err_t result = nana_session_audio_abort(
            stream_id,
            cJSON_IsString(reason) ? reason->valuestring : "core_abort");
        cJSON_Delete(root);
        if (result != ESP_OK) {
            mark_protocol_error(context, "playback_abort was rejected");
        }
        return;
    }

    cJSON_Delete(root);
    mark_protocol_error(context, "message type is not enabled");
}

static void process_data_event(
    nana_session_context_t *context,
    const esp_websocket_event_data_t *data)
{
    if (data->op_code >= 0x08U) {
        if (data->op_code == 0x08U) {
            xEventGroupSetBits(context->events, SESSION_DISCONNECTED_BIT);
        }
        return;
    }
    if (data->payload_len <= 0 ||
        data->payload_len >= (int)sizeof(context->rx) ||
        data->payload_offset < 0 || data->data_len < 0 ||
        data->data_ptr == NULL) {
        mark_protocol_error(context, "invalid bounded WebSocket frame");
        return;
    }
    if (data->payload_offset == 0) {
        if (data->op_code != 0x01U && data->op_code != 0x02U) {
            mark_protocol_error(context, "unsupported WebSocket opcode");
            return;
        }
        reset_rx(context);
        context->rx_expected = (size_t)data->payload_len;
        context->rx_opcode = (uint8_t)data->op_code;
    } else if (data->op_code != 0x00U &&
               data->op_code != context->rx_opcode) {
        mark_protocol_error(context, "fragment opcode mismatch");
        return;
    }
    if (context->rx_expected != (size_t)data->payload_len ||
        context->rx_used != (size_t)data->payload_offset ||
        context->rx_used + (size_t)data->data_len > context->rx_expected) {
        mark_protocol_error(context, "invalid text frame fragmentation");
        return;
    }
    memcpy(
        context->rx + context->rx_used,
        data->data_ptr,
        (size_t)data->data_len);
    context->rx_used += (size_t)data->data_len;
    if (context->rx_used == context->rx_expected) {
        if (context->rx_opcode == 0x01U) {
            context->rx[context->rx_used] = '\0';
            process_text_message(context, (const char *)context->rx);
        } else {
            const esp_err_t result = nana_session_audio_enqueue_frame(
                context->rx,
                context->rx_used);
            if (result != ESP_OK) {
                mark_protocol_error(context, "binary audio frame was rejected");
                return;
            }
        }
        reset_rx(context);
    }
}

static void websocket_event_handler(
    void *handler_args,
    esp_event_base_t event_base,
    int32_t event_id,
    void *event_data)
{
    (void)event_base;
    nana_session_context_t *context =
        (nana_session_context_t *)handler_args;
    esp_websocket_event_data_t *data =
        (esp_websocket_event_data_t *)event_data;

    switch (event_id) {
    case WEBSOCKET_EVENT_CONNECTED:
        reset_rx(context);
        xEventGroupSetBits(context->events, SESSION_CONNECTED_BIT);
        break;
    case WEBSOCKET_EVENT_DISCONNECTED:
    case WEBSOCKET_EVENT_CLOSED:
        xEventGroupSetBits(context->events, SESSION_DISCONNECTED_BIT);
        break;
    case WEBSOCKET_EVENT_DATA:
        process_data_event(context, data);
        break;
    case WEBSOCKET_EVENT_ERROR:
        ESP_LOGE(TAG,
                 "WebSocket error | type=%d | http=%d | tls=%s | socket_errno=%d",
                 data->error_handle.error_type,
                 data->error_handle.esp_ws_handshake_status_code,
                 esp_err_to_name(data->error_handle.esp_tls_last_esp_err),
                 data->error_handle.esp_transport_sock_errno);
        xEventGroupSetBits(context->events, SESSION_PROTOCOL_ERROR_BIT);
        break;
    default:
        break;
    }
}

static void build_device_id(char *output, size_t capacity)
{
    uint8_t mac[6] = {0};
    if (esp_read_mac(mac, ESP_MAC_WIFI_STA) != ESP_OK) {
        strlcpy(output, "nana-presence-unknown", capacity);
        return;
    }
    snprintf(
        output,
        capacity,
        "nana-presence-%02x%02x%02x%02x%02x%02x",
        mac[0], mac[1], mac[2], mac[3], mac[4], mac[5]);
}

static esp_err_t send_text(
    esp_websocket_client_handle_t client, const char *message)
{
    const size_t length = strlen(message);
    const int sent = esp_websocket_client_send_text(
        client,
        message,
        (int)length,
        pdMS_TO_TICKS(NANA_SESSION_SEND_TIMEOUT_MS));
    return sent == (int)length ? ESP_OK : ESP_FAIL;
}

static esp_err_t drain_session_audio_credits(
    esp_websocket_client_handle_t client,
    nana_session_context_t *context)
{
    uint32_t stream_id = 0U;
    uint32_t credits = 0U;
    uint32_t bytes_received = 0U;

    portENTER_CRITICAL(&context->credit_lock);
    if (context->pending_credit_count > 0U) {
        stream_id = context->pending_credit_stream_id;
        credits = context->pending_credit_count;
        if (credits > NANA_SESSION_AUDIO_CREDIT_BATCH_MAX) {
            credits = NANA_SESSION_AUDIO_CREDIT_BATCH_MAX;
        }
        bytes_received = context->pending_credit_bytes;
        context->pending_credit_count -= credits;
        if (context->pending_credit_count == 0U) {
            context->pending_credit_stream_id = 0U;
            context->pending_credit_bytes = 0U;
        }
    }
    portEXIT_CRITICAL(&context->credit_lock);

    if (credits == 0U) {
        return ESP_OK;
    }

    char message[224] = {0};
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"playback_credit\","
        "\"protocol\":\"%s\",\"stream_id\":%" PRIu32
        ",\"credits\":%" PRIu32 ",\"bytes_played\":%" PRIu32 "}",
        NANA_SESSION_PROTOCOL,
        stream_id,
        credits,
        bytes_received);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return send_text(client, message);
}

static esp_err_t send_queued_message(
    esp_websocket_client_handle_t client,
    const nana_session_tx_message_t *message)
{
    if (message == NULL || message->length == 0U) {
        return ESP_ERR_INVALID_ARG;
    }
    int sent = -1;
    if (message->opcode == 0x01U) {
        sent = esp_websocket_client_send_text(
            client,
            (const char *)message->payload,
            message->length,
            pdMS_TO_TICKS(NANA_SESSION_SEND_TIMEOUT_MS));
    } else if (message->opcode == 0x02U) {
        sent = esp_websocket_client_send_bin(
            client,
            (const char *)message->payload,
            message->length,
            pdMS_TO_TICKS(NANA_SESSION_SEND_TIMEOUT_MS));
    } else {
        return ESP_ERR_INVALID_ARG;
    }
    return sent == (int)message->length ? ESP_OK : ESP_FAIL;
}

static esp_err_t drain_session_tx(
    esp_websocket_client_handle_t client,
    nana_session_context_t *context)
{
    nana_session_tx_message_t message = {0};
    while (xQueueReceive(context->tx_queue, &message, 0) == pdTRUE) {
        const esp_err_t result = send_queued_message(client, &message);
        if (result != ESP_OK) {
            return result;
        }
    }
    return ESP_OK;
}

static esp_err_t send_hello(esp_websocket_client_handle_t client)
{
    char device_id[48] = {0};
    char message[NANA_SESSION_JSON_BYTES] = {0};
    build_device_id(device_id, sizeof(device_id));
    const esp_app_desc_t *description = esp_app_get_description();
    const char *firmware =
        description != NULL && description->version[0] != '\0'
            ? description->version
            : "unknown";

    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"hello\",\"protocol\":\"%s\","
        "\"device_id\":\"%s\",\"firmware\":\"%s\","
        "\"profile\":\"%s\",\"capabilities\":{"
        "\"audio_uplink\":true,\"audio_downlink\":true,"
        "\"audio_downlink_stream\":true,"
        "\"camera\":%s,\"display\":%s}}",
        NANA_SESSION_PROTOCOL,
        device_id,
        firmware,
        NANA_SESSION_PROFILE,
        nana_session_camera_available() ? "true" : "false",
        nana_display_is_running() ? "true" : "false");
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return send_text(client, message);
}

static esp_err_t send_heartbeat(
    esp_websocket_client_handle_t client, uint32_t sequence)
{
    char message[192] = {0};
    const uint64_t uptime_ms = (uint64_t)(esp_timer_get_time() / 1000);
    const int written = snprintf(
        message,
        sizeof(message),
        "{\"type\":\"heartbeat\",\"protocol\":\"%s\","
        "\"sequence\":%" PRIu32 ",\"uptime_ms\":%" PRIu64 "}",
        NANA_SESSION_PROTOCOL,
        sequence,
        uptime_ms);
    if (written <= 0 || (size_t)written >= sizeof(message)) {
        return ESP_ERR_INVALID_SIZE;
    }
    return send_text(client, message);
}

static esp_err_t run_connection_attempt(
    const nana_session_config_t *config,
    nana_session_context_t *context,
    uint32_t *sequence)
{
    const esp_err_t wifi_result =
        nana_wifi_wait_connected(NANA_SESSION_CONNECT_TIMEOUT_MS);
    if (wifi_result != ESP_OK) {
        return wifi_result;
    }

    char headers[NANA_SESSION_HEADERS_BYTES] = {0};
    const int header_length = snprintf(
        headers,
        sizeof(headers),
        "Authorization: Bearer %s\r\n",
        config->token);
    if (header_length <= 0 || (size_t)header_length >= sizeof(headers)) {
        return ESP_ERR_INVALID_SIZE;
    }

    context->heartbeat_ms = NANA_SESSION_HEARTBEAT_DEFAULT_MS;
    context->session_id[0] = '\0';
    context->session_ready = false;
    reset_rx(context);
    reset_session_audio_credits(context);
    xQueueReset(context->tx_queue);
    xEventGroupClearBits(
        context->events,
        SESSION_CONNECTED_BIT | SESSION_DISCONNECTED_BIT |
            SESSION_WELCOME_BIT | SESSION_ACK_BIT |
            SESSION_PROTOCOL_ERROR_BIT);

    const esp_websocket_client_config_t client_config = {
        .uri = config->uri,
        .disable_auto_reconnect = true,
        .task_prio = 5,
        .task_name = "nana_session_ws",
        .task_stack = 6144,
        .buffer_size = 2048,
        .user_agent = "Nana-Presence-Node/1",
        .headers = headers,
        .pingpong_timeout_sec = 15,
        .keep_alive_enable = true,
        .keep_alive_idle = 5,
        .keep_alive_interval = 5,
        .keep_alive_count = 3,
        .network_timeout_ms = NANA_SESSION_CONNECT_TIMEOUT_MS,
        .ping_interval_sec = 10,
    };
    esp_websocket_client_handle_t client =
        esp_websocket_client_init(&client_config);
    if (client == NULL) {
        return ESP_ERR_NO_MEM;
    }

    esp_err_t result = esp_websocket_register_events(
        client,
        WEBSOCKET_EVENT_ANY,
        websocket_event_handler,
        context);
    bool started = false;
    if (result == ESP_OK) {
        result = esp_websocket_client_start(client);
        started = result == ESP_OK;
    }
    if (result != ESP_OK) {
        goto cleanup;
    }

    EventBits_t bits = xEventGroupWaitBits(
        context->events,
        SESSION_CONNECTED_BIT | SESSION_DISCONNECTED_BIT |
            SESSION_PROTOCOL_ERROR_BIT,
        pdTRUE,
        pdFALSE,
        pdMS_TO_TICKS(NANA_SESSION_CONNECT_TIMEOUT_MS));
    if ((bits & SESSION_CONNECTED_BIT) == 0U) {
        result = (bits & SESSION_PROTOCOL_ERROR_BIT) != 0U
                     ? ESP_ERR_INVALID_RESPONSE
                     : ESP_ERR_TIMEOUT;
        goto cleanup;
    }

    result = send_hello(client);
    if (result != ESP_OK) {
        goto cleanup;
    }
    bits = xEventGroupWaitBits(
        context->events,
        SESSION_WELCOME_BIT | SESSION_DISCONNECTED_BIT |
            SESSION_PROTOCOL_ERROR_BIT,
        pdTRUE,
        pdFALSE,
        pdMS_TO_TICKS(NANA_SESSION_HELLO_TIMEOUT_MS));
    if ((bits & SESSION_WELCOME_BIT) == 0U) {
        result = (bits & SESSION_PROTOCOL_ERROR_BIT) != 0U
                     ? ESP_ERR_INVALID_RESPONSE
                     : ESP_ERR_TIMEOUT;
        goto cleanup;
    }

    context->session_ready = true;
    nana_session_uplink_connected();
    nana_session_camera_connected();

    ESP_LOGI(TAG,
             "NANA_SESSION_CONNECTED protocol=%s session=%s heartbeat=%" PRIu32 "ms",
             NANA_SESSION_PROTOCOL,
             context->session_id,
             context->heartbeat_ms);

    bool awaiting_ack = false;
    TickType_t ack_started_at = 0;
    TickType_t next_heartbeat_at = xTaskGetTickCount();
    while (esp_websocket_client_is_connected(client)) {
        bits = xEventGroupWaitBits(
            context->events,
            SESSION_ACK_BIT | SESSION_DISCONNECTED_BIT |
                SESSION_PROTOCOL_ERROR_BIT,
            pdTRUE,
            pdFALSE,
            pdMS_TO_TICKS(20));
        if ((bits & SESSION_DISCONNECTED_BIT) != 0U) {
            result = ESP_ERR_INVALID_STATE;
            break;
        }
        if ((bits & SESSION_PROTOCOL_ERROR_BIT) != 0U) {
            result = ESP_ERR_INVALID_RESPONSE;
            break;
        }
        if ((bits & SESSION_ACK_BIT) != 0U) {
            awaiting_ack = false;
        }

        result = drain_session_tx(client, context);
        if (result != ESP_OK) {
            break;
        }
        result = drain_session_audio_credits(client, context);
        if (result != ESP_OK) {
            break;
        }

        const TickType_t now = xTaskGetTickCount();
        if (awaiting_ack &&
            (now - ack_started_at) >=
                pdMS_TO_TICKS(NANA_SESSION_ACK_TIMEOUT_MS)) {
            result = ESP_ERR_TIMEOUT;
            break;
        }
        if (!awaiting_ack &&
            (int32_t)(now - next_heartbeat_at) >= 0) {
            ++(*sequence);
            context->expected_sequence = *sequence;
            xEventGroupClearBits(context->events, SESSION_ACK_BIT);
            result = send_heartbeat(client, *sequence);
            if (result != ESP_OK) {
                break;
            }
            awaiting_ack = true;
            ack_started_at = now;
            next_heartbeat_at = now + pdMS_TO_TICKS(context->heartbeat_ms);
            if ((*sequence % 6U) == 0U) {
                ESP_LOGI(TAG,
                         "NANA_SESSION_HEALTH sequence=%" PRIu32
                         " free_heap=%" PRIu32,
                         *sequence,
                         esp_get_free_heap_size());
            }
        }
    }
    if (result == ESP_OK) {
        result = ESP_ERR_INVALID_STATE;
    }

cleanup:
    context->session_ready = false;
    nana_session_camera_disconnected();
    nana_session_uplink_disconnected();
    nana_session_audio_connection_closed();
    reset_session_audio_credits(context);
    if (started) {
        const esp_err_t stop_result = esp_websocket_client_stop(client);
        if (result == ESP_OK && stop_result != ESP_OK &&
            stop_result != ESP_ERR_INVALID_STATE) {
            result = stop_result;
        }
    }
    const esp_err_t destroy_result = esp_websocket_client_destroy(client);
    if (result == ESP_OK && destroy_result != ESP_OK) {
        result = destroy_result;
    }
    return result;
}

esp_err_t nana_presence_session_run(void)
{
    ESP_LOGI(
        TAG,
        "NANA_SESSION_SUPERVISOR state=starting tx_queue=%u reconnect_max=%ums heartbeat_watchdog=%ums",
        (unsigned)NANA_SESSION_TX_QUEUE_DEPTH,
        (unsigned)NANA_SESSION_BACKOFF_MAX_MS,
        (unsigned)NANA_SESSION_ACK_TIMEOUT_MS);
    esp_err_t result = initialize_serial_input();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Session provisioning input failed: %s",
                 esp_err_to_name(result));
        return result;
    }

    nana_session_config_t config = {0};
    result = load_config(&config);
    if (result != ESP_OK) {
        ESP_LOGW(TAG,
                 "No valid Presence session config; waiting for one-time USB provisioning");
        result = wait_for_initial_config(&config);
        if (result != ESP_OK) {
            return result;
        }
    }
    result = start_serial_listener();
    if (result != ESP_OK) {
        return result;
    }

    nana_session_context_t context = {
        .events = xEventGroupCreate(),
        .tx_queue = xQueueCreate(
            NANA_SESSION_TX_QUEUE_DEPTH,
            sizeof(nana_session_tx_message_t)),
        .heartbeat_ms = NANA_SESSION_HEARTBEAT_DEFAULT_MS,
        .credit_lock = portMUX_INITIALIZER_UNLOCKED,
    };
    nana_session_supervisor_t supervisor = {0};
    if (context.events == NULL || context.tx_queue == NULL) {
        return fail_session_start(ESP_ERR_NO_MEM, &context, &supervisor);
    }
    result = nana_session_audio_init(
        queue_session_text,
        queue_session_audio_credit,
        &context);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Session audio worker init failed: %s",
                 esp_err_to_name(result));
        return fail_session_start(result, &context, &supervisor);
    }
    supervisor.audio_started = true;
    result = nana_session_uplink_init(
        queue_session_text, queue_session_binary, &context);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Session microphone worker init failed: %s",
                 esp_err_to_name(result));
        return fail_session_start(result, &context, &supervisor);
    }
    supervisor.uplink_started = true;
    result = nana_session_camera_init(
        queue_session_text, queue_session_binary, &context);
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Session camera worker init failed: %s",
                 esp_err_to_name(result));
        return fail_session_start(result, &context, &supervisor);
    }
    supervisor.camera_started = true;

    ESP_LOGI(TAG,
             "Presence session bounded PCM half-duplex + one-shot JPEG | uri=%s | token=hidden | pcm=s16le/16k/mono",
             config.uri);
    ESP_LOGI(
        TAG,
        "NANA_SESSION_SUPERVISOR state=ready workers=audio,uplink,camera queue_policy=bounded mute_policy=fail_closed");
    uint32_t sequence = 0U;
    uint32_t backoff_ms = 1000U;
    while (true) {
        result = run_connection_attempt(&config, &context, &sequence);
        const uint32_t delay_ms =
            backoff_ms + (esp_random() % NANA_SESSION_BACKOFF_JITTER_MS);
        ESP_LOGW(TAG,
                 "NANA_SESSION_RECONNECT reason=%s delay=%" PRIu32 "ms",
                 esp_err_to_name(result),
                 delay_ms);
        vTaskDelay(pdMS_TO_TICKS(delay_ms));
        if (context.session_id[0] != '\0') {
            backoff_ms = 1000U;
        } else if (backoff_ms < NANA_SESSION_BACKOFF_MAX_MS) {
            backoff_ms *= 2U;
            if (backoff_ms > NANA_SESSION_BACKOFF_MAX_MS) {
                backoff_ms = NANA_SESSION_BACKOFF_MAX_MS;
            }
        }
    }
}
