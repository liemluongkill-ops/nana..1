#include <stdbool.h>
#include <inttypes.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "esp_http_server.h"
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "nvs.h"

#include "nana_camera.h"
#include "nana_camera_http.h"

#define NANA_CAMERA_TOKEN_NAMESPACE "nana_camera"
#define NANA_CAMERA_TOKEN_KEY "token"
#define NANA_CAMERA_TOKEN_BYTES 16
#define NANA_CAMERA_TOKEN_HEX_BYTES (NANA_CAMERA_TOKEN_BYTES * 2 + 1)
#define NANA_CAMERA_STREAM_INTERVAL_MS 200
#define NANA_CAMERA_FRAME_TIMEOUT_MS 2000
#define NANA_CAMERA_STREAM_BOUNDARY "nana_presence_frame"
#define NANA_CAMERA_AUDIO_GATE_LEASE_MS 5000

static const char *TAG = "nana_camera_http";
static const char stream_content_type[] =
    "multipart/x-mixed-replace;boundary=" NANA_CAMERA_STREAM_BOUNDARY;
static const char stream_boundary[] =
    "\r\n--" NANA_CAMERA_STREAM_BOUNDARY "\r\n";
static const char stream_part_header[] =
    "Content-Type: image/jpeg\r\nContent-Length: %zu\r\n\r\n";

static httpd_handle_t camera_server = NULL;
static char camera_access_token[NANA_CAMERA_TOKEN_HEX_BYTES] = {0};
static uint32_t frames_served = 0;
static uint32_t snapshots_served = 0;
static bool stream_active = false;
static bool audio_allowed = false;
static int64_t audio_gate_expires_us = 0;
static portMUX_TYPE audio_gate_lock = portMUX_INITIALIZER_UNLOCKED;
static int64_t server_started_us = 0;

static void set_audio_gate(bool allowed)
{
    const int64_t expires_us =
        allowed
            ? esp_timer_get_time() +
                  (int64_t)NANA_CAMERA_AUDIO_GATE_LEASE_MS * 1000
            : 0;
    portENTER_CRITICAL(&audio_gate_lock);
    audio_allowed = allowed;
    audio_gate_expires_us = expires_us;
    portEXIT_CRITICAL(&audio_gate_lock);
}

bool nana_camera_http_audio_allowed(void)
{
    const int64_t now_us = esp_timer_get_time();
    bool allowed = false;
    portENTER_CRITICAL(&audio_gate_lock);
    if (audio_allowed && now_us < audio_gate_expires_us) {
        allowed = true;
    } else {
        audio_allowed = false;
        audio_gate_expires_us = 0;
    }
    portEXIT_CRITICAL(&audio_gate_lock);
    return allowed;
}

static esp_err_t send_service_unavailable(
    httpd_req_t *request,
    const char *message)
{
    esp_err_t result = httpd_resp_set_status(
        request, "503 Service Unavailable");
    if (result == ESP_OK) {
        result = httpd_resp_set_type(request, "text/plain");
    }
    if (result == ESP_OK) {
        result = httpd_resp_sendstr(request, message);
    }
    return result;
}

static void bytes_to_hex(const uint8_t *bytes, size_t length, char *hex)
{
    static const char digits[] = "0123456789abcdef";
    for (size_t index = 0; index < length; ++index) {
        hex[index * 2] = digits[bytes[index] >> 4];
        hex[index * 2 + 1] = digits[bytes[index] & 0x0f];
    }
    hex[length * 2] = '\0';
}

static esp_err_t load_or_create_access_token(void)
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(
        NANA_CAMERA_TOKEN_NAMESPACE, NVS_READWRITE, &handle);
    if (result != ESP_OK) {
        return result;
    }

    size_t token_length = sizeof(camera_access_token);
    result = nvs_get_str(
        handle,
        NANA_CAMERA_TOKEN_KEY,
        camera_access_token,
        &token_length);
    if (result == ESP_OK &&
        token_length == sizeof(camera_access_token)) {
        nvs_close(handle);
        return ESP_OK;
    }

    uint8_t token_bytes[NANA_CAMERA_TOKEN_BYTES] = {0};
    esp_fill_random(token_bytes, sizeof(token_bytes));
    bytes_to_hex(
        token_bytes, sizeof(token_bytes), camera_access_token);

    result = nvs_set_str(
        handle, NANA_CAMERA_TOKEN_KEY, camera_access_token);
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}

static bool constant_time_token_equal(const char *candidate)
{
    if (candidate == NULL ||
        strlen(candidate) != strlen(camera_access_token)) {
        return false;
    }

    unsigned char difference = 0;
    for (size_t index = 0; camera_access_token[index] != '\0'; ++index) {
        difference |= (unsigned char)(
            candidate[index] ^ camera_access_token[index]);
    }
    return difference == 0;
}

static bool request_has_valid_token(httpd_req_t *request)
{
    const size_t header_length =
        httpd_req_get_hdr_value_len(request, "X-Nana-Token");
    if (header_length > 0 && header_length < 64) {
        char header[64] = {0};
        if (httpd_req_get_hdr_value_str(
                request,
                "X-Nana-Token",
                header,
                sizeof(header)) == ESP_OK &&
            constant_time_token_equal(header)) {
            return true;
        }
    }

    const size_t query_length = httpd_req_get_url_query_len(request);
    if (query_length == 0 || query_length >= 128) {
        return false;
    }

    char query[128] = {0};
    char token[64] = {0};
    return httpd_req_get_url_query_str(
               request, query, sizeof(query)) == ESP_OK &&
           httpd_query_key_value(
               query, "token", token, sizeof(token)) == ESP_OK &&
           constant_time_token_equal(token);
}

static esp_err_t require_token(httpd_req_t *request)
{
    if (request_has_valid_token(request)) {
        return ESP_OK;
    }

    httpd_resp_set_status(request, "401 Unauthorized");
    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_sendstr(
        request, "{\"error\":\"unauthorized\"}");
}

static esp_err_t root_handler(httpd_req_t *request)
{
    if (!request_has_valid_token(request)) {
        return require_token(request);
    }

    char page[1400] = {0};
    const int length = snprintf(
        page,
        sizeof(page),
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
        "<title>Nana Camera</title><style>"
        "html,body{margin:0;width:100%%;height:100%%;background:#111;color:#eee;"
        "font-family:system-ui,sans-serif}main{width:100%%;height:100%%;display:grid;"
        "place-items:center;overflow:hidden}img{width:100%%;height:100%%;"
        "object-fit:contain;image-rendering:auto}aside{position:fixed;left:12px;"
        "bottom:10px;background:#111c;padding:6px 8px;border-radius:4px;"
        "font-size:12px}</style></head><body><main>"
        "<img src=\"/stream.mjpg?token=%s\" alt=\"Nana camera stream\">"
        "</main><aside>OV5640 | %dx%d | LAN</aside></body></html>",
        camera_access_token,
        NANA_CAMERA_FRAME_WIDTH,
        NANA_CAMERA_FRAME_HEIGHT);
    if (length < 0 || (size_t)length >= sizeof(page)) {
        return httpd_resp_send_err(
            request, HTTPD_500_INTERNAL_SERVER_ERROR, "page_overflow");
    }

    httpd_resp_set_type(request, "text/html; charset=utf-8");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_send(request, page, (ssize_t)length);
}

static esp_err_t health_handler(httpd_req_t *request)
{
    if (!request_has_valid_token(request)) {
        return require_token(request);
    }

    char payload[320] = {0};
    const int64_t uptime_ms =
        (esp_timer_get_time() - server_started_us) / 1000;
    const int length = snprintf(
        payload,
        sizeof(payload),
        "{\"status\":\"ok\",\"sensor\":\"OV5640\","
        "\"width\":%d,\"height\":%d,\"format\":\"jpeg\","
        "\"stream_active\":%s,\"audio_allowed\":%s,"
        "\"frames_served\":%" PRIu32 ","
        "\"snapshots_served\":%" PRIu32 ",\"uptime_ms\":%" PRId64 "}",
        NANA_CAMERA_FRAME_WIDTH,
        NANA_CAMERA_FRAME_HEIGHT,
        __atomic_load_n(&stream_active, __ATOMIC_RELAXED) ? "true" : "false",
        nana_camera_http_audio_allowed() ? "true" : "false",
        __atomic_load_n(&frames_served, __ATOMIC_RELAXED),
        __atomic_load_n(&snapshots_served, __ATOMIC_RELAXED),
        uptime_ms);
    if (length < 0 || (size_t)length >= sizeof(payload)) {
        return httpd_resp_send_err(
            request, HTTPD_500_INTERNAL_SERVER_ERROR, "health_overflow");
    }

    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_send(request, payload, (ssize_t)length);
}

static esp_err_t audio_gate_handler(httpd_req_t *request)
{
    if (!request_has_valid_token(request)) {
        return require_token(request);
    }

    const size_t query_length = httpd_req_get_url_query_len(request);
    if (query_length == 0 || query_length >= 96) {
        return httpd_resp_send_err(
            request, HTTPD_400_BAD_REQUEST, "missing_allowed");
    }

    char query[96] = {0};
    char allowed_value[8] = {0};
    if (httpd_req_get_url_query_str(
            request, query, sizeof(query)) != ESP_OK ||
        httpd_query_key_value(
            query,
            "allowed",
            allowed_value,
            sizeof(allowed_value)) != ESP_OK) {
        return httpd_resp_send_err(
            request, HTTPD_400_BAD_REQUEST, "missing_allowed");
    }

    bool allowed = false;
    if (strcmp(allowed_value, "1") == 0 ||
        strcmp(allowed_value, "true") == 0) {
        allowed = true;
    } else if (strcmp(allowed_value, "0") != 0 &&
               strcmp(allowed_value, "false") != 0) {
        return httpd_resp_send_err(
            request, HTTPD_400_BAD_REQUEST, "invalid_allowed");
    }

    const bool previous = nana_camera_http_audio_allowed();
    set_audio_gate(allowed);
    if (previous != allowed) {
        ESP_LOGI(TAG,
                 "Presence audio gate: %s",
                 allowed ? "interactive" : "display_only");
    }

    httpd_resp_set_type(request, "application/json");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    return httpd_resp_sendstr(
        request,
        allowed
            ? "{\"audio_allowed\":true}"
            : "{\"audio_allowed\":false}");
}

static esp_err_t capture_handler(httpd_req_t *request)
{
    if (!request_has_valid_token(request)) {
        return require_token(request);
    }

    camera_fb_t *frame = nana_camera_acquire_frame(
        pdMS_TO_TICKS(NANA_CAMERA_FRAME_TIMEOUT_MS));
    if (frame == NULL) {
        return send_service_unavailable(request, "camera_busy");
    }
    if (!nana_camera_frame_is_complete_jpeg(frame)) {
        nana_camera_release_frame(frame);
        return httpd_resp_send_err(
            request, HTTPD_500_INTERNAL_SERVER_ERROR, "invalid_jpeg");
    }

    httpd_resp_set_type(request, "image/jpeg");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    const esp_err_t result = httpd_resp_send(
        request, (const char *)frame->buf, (ssize_t)frame->len);
    nana_camera_release_frame(frame);
    if (result == ESP_OK) {
        __atomic_add_fetch(&snapshots_served, 1, __ATOMIC_RELAXED);
    }
    return result;
}

static esp_err_t stream_handler(httpd_req_t *request)
{
    if (!request_has_valid_token(request)) {
        return require_token(request);
    }
    if (__atomic_exchange_n(&stream_active, true, __ATOMIC_ACQ_REL)) {
        return send_service_unavailable(request, "stream_already_active");
    }

    esp_err_t result = httpd_resp_set_type(request, stream_content_type);
    if (result == ESP_OK) {
        result = httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    }
    ESP_LOGI(TAG, "MJPEG client connected");

    while (result == ESP_OK) {
        camera_fb_t *frame = nana_camera_acquire_frame(
            pdMS_TO_TICKS(NANA_CAMERA_FRAME_TIMEOUT_MS));
        if (frame == NULL) {
            result = ESP_ERR_TIMEOUT;
            break;
        }
        if (!nana_camera_frame_is_complete_jpeg(frame)) {
            nana_camera_release_frame(frame);
            result = ESP_ERR_INVALID_RESPONSE;
            break;
        }

        char part_header[96] = {0};
        const int header_length = snprintf(
            part_header,
            sizeof(part_header),
            stream_part_header,
            frame->len);
        if (header_length < 0 ||
            (size_t)header_length >= sizeof(part_header)) {
            nana_camera_release_frame(frame);
            result = ESP_ERR_INVALID_SIZE;
            break;
        }

        result = httpd_resp_send_chunk(
            request, stream_boundary, strlen(stream_boundary));
        if (result == ESP_OK) {
            result = httpd_resp_send_chunk(
                request, part_header, (size_t)header_length);
        }
        if (result == ESP_OK) {
            result = httpd_resp_send_chunk(
                request, (const char *)frame->buf, frame->len);
        }
        nana_camera_release_frame(frame);

        if (result == ESP_OK) {
            __atomic_add_fetch(&frames_served, 1, __ATOMIC_RELAXED);
            vTaskDelay(pdMS_TO_TICKS(NANA_CAMERA_STREAM_INTERVAL_MS));
        }
    }

    __atomic_store_n(&stream_active, false, __ATOMIC_RELEASE);
    ESP_LOGI(TAG, "MJPEG client disconnected | status=%s",
             esp_err_to_name(result));
    return result;
}

esp_err_t nana_camera_http_start(const char *ip_address)
{
    if (camera_server != NULL) {
        return ESP_OK;
    }

    esp_err_t result = nana_camera_start();
    if (result != ESP_OK) {
        return result;
    }
    result = load_or_create_access_token();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "Camera access token init failed: %s",
                 esp_err_to_name(result));
        return result;
    }

    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.max_uri_handlers = 6;
    config.lru_purge_enable = true;
    config.stack_size = 8192;
    config.recv_wait_timeout = 10;
    config.send_wait_timeout = 10;

    result = httpd_start(&camera_server, &config);
    if (result != ESP_OK) {
        return result;
    }

    const httpd_uri_t root_uri = {
        .uri = "/",
        .method = HTTP_GET,
        .handler = root_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t health_uri = {
        .uri = "/health",
        .method = HTTP_GET,
        .handler = health_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t capture_uri = {
        .uri = "/capture.jpg",
        .method = HTTP_GET,
        .handler = capture_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t stream_uri = {
        .uri = "/stream.mjpg",
        .method = HTTP_GET,
        .handler = stream_handler,
        .user_ctx = NULL,
    };
    const httpd_uri_t audio_gate_uri = {
        .uri = "/audio-gate",
        .method = HTTP_POST,
        .handler = audio_gate_handler,
        .user_ctx = NULL,
    };

    result = httpd_register_uri_handler(camera_server, &root_uri);
    if (result == ESP_OK) {
        result = httpd_register_uri_handler(camera_server, &health_uri);
    }
    if (result == ESP_OK) {
        result = httpd_register_uri_handler(camera_server, &capture_uri);
    }
    if (result == ESP_OK) {
        result = httpd_register_uri_handler(camera_server, &stream_uri);
    }
    if (result == ESP_OK) {
        result = httpd_register_uri_handler(camera_server, &audio_gate_uri);
    }
    if (result != ESP_OK) {
        httpd_stop(camera_server);
        camera_server = NULL;
        return result;
    }

    server_started_us = esp_timer_get_time();
    ESP_LOGI(TAG,
             "Camera HTTP service: PASS | one active MJPEG client | 5fps target");
    printf("NANA_CAMERA_ACCESS_TOKEN=%s\n", camera_access_token);
    printf("NANA_CAMERA_VIEW=http://%s/?token=%s\n",
           ip_address,
           camera_access_token);
    printf("NANA_CAMERA_CAPTURE=http://%s/capture.jpg\n", ip_address);
    fflush(stdout);
    return ESP_OK;
}
