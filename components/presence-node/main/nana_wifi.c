#include <stdbool.h>
#include <stddef.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "driver/uart.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_netif.h"
#include "esp_system.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"
#include "lwip/ip4_addr.h"
#include "mbedtls/base64.h"
#include "nvs.h"
#include "nvs_flash.h"

#include "nana_wifi.h"

#define NANA_WIFI_NAMESPACE "nana_wifi"
#define NANA_WIFI_SSID_KEY "ssid"
#define NANA_WIFI_PASSWORD_KEY "password"
#define NANA_WIFI_MAX_RETRIES 20
#define NANA_WIFI_SERIAL_LINE_BYTES 256
#define NANA_WIFI_UART_BUFFER_BYTES 1024
#define NANA_WIFI_MAX_TX_POWER_QDBM 40

#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAILED_BIT BIT1

typedef struct {
    char ssid[33];
    char password[65];
} nana_wifi_credentials_t;

static const char *TAG = "nana_wifi";
static EventGroupHandle_t wifi_events = NULL;
static int wifi_retry_count = 0;
static char wifi_ip_address[IP4ADDR_STRLEN_MAX] = "0.0.0.0";
static bool serial_listener_started = false;
static volatile bool wifi_reconnect_suspended = false;

static esp_err_t initialize_nvs(void)
{
    esp_err_t result = nvs_flash_init();
    if (result == ESP_ERR_NVS_NO_FREE_PAGES ||
        result == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        result = nvs_flash_init();
    }
    return result;
}

static esp_err_t initialize_serial_input(void)
{
    const esp_err_t result = uart_driver_install(
        UART_NUM_0,
        NANA_WIFI_UART_BUFFER_BYTES,
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
    if (line == NULL || capacity < 2) {
        return false;
    }

    const bool wait_forever = timeout == portMAX_DELAY;
    const TickType_t started_at = xTaskGetTickCount();
    size_t length = 0;

    while (wait_forever || (xTaskGetTickCount() - started_at) < timeout) {
        uint8_t byte = 0;
        const int bytes_read = uart_read_bytes(
            UART_NUM_0, &byte, 1, pdMS_TO_TICKS(100));
        if (bytes_read <= 0) {
            continue;
        }
        if (byte == '\r') {
            continue;
        }
        if (byte == '\n') {
            if (length == 0) {
                continue;
            }
            line[length] = '\0';
            return true;
        }
        if (length + 1 < capacity) {
            line[length++] = (char)byte;
        } else {
            length = 0;
        }
    }
    return false;
}

static bool decode_serial_field(const char *encoded,
                                char *decoded,
                                size_t decoded_capacity)
{
    if (encoded == NULL || decoded == NULL || decoded_capacity < 2) {
        return false;
    }
    if (strcmp(encoded, "-") == 0) {
        decoded[0] = '\0';
        return true;
    }

    size_t decoded_length = 0;
    const int result = mbedtls_base64_decode(
        (unsigned char *)decoded,
        decoded_capacity - 1,
        &decoded_length,
        (const unsigned char *)encoded,
        strlen(encoded));
    if (result != 0 || decoded_length >= decoded_capacity) {
        return false;
    }
    decoded[decoded_length] = '\0';
    return true;
}

static bool parse_set_command(char *line, nana_wifi_credentials_t *credentials)
{
    static const char prefix[] = "NANA_WIFI_SET ";
    if (line == NULL || credentials == NULL ||
        strncmp(line, prefix, sizeof(prefix) - 1) != 0) {
        return false;
    }

    char *save_pointer = NULL;
    char *ssid_encoded = strtok_r(line + sizeof(prefix) - 1, " ", &save_pointer);
    char *password_encoded = strtok_r(NULL, " ", &save_pointer);
    if (ssid_encoded == NULL || password_encoded == NULL ||
        strtok_r(NULL, " ", &save_pointer) != NULL) {
        return false;
    }

    nana_wifi_credentials_t parsed = {0};
    if (!decode_serial_field(
            ssid_encoded, parsed.ssid, sizeof(parsed.ssid)) ||
        !decode_serial_field(
            password_encoded, parsed.password, sizeof(parsed.password)) ||
        parsed.ssid[0] == '\0') {
        return false;
    }
    *credentials = parsed;
    return true;
}

static esp_err_t load_credentials(nana_wifi_credentials_t *credentials)
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(NANA_WIFI_NAMESPACE, NVS_READONLY, &handle);
    if (result != ESP_OK) {
        return result;
    }

    size_t ssid_length = sizeof(credentials->ssid);
    size_t password_length = sizeof(credentials->password);
    result = nvs_get_str(
        handle, NANA_WIFI_SSID_KEY, credentials->ssid, &ssid_length);
    if (result == ESP_OK) {
        result = nvs_get_str(
            handle,
            NANA_WIFI_PASSWORD_KEY,
            credentials->password,
            &password_length);
    }
    nvs_close(handle);
    return result;
}

static esp_err_t save_credentials(const nana_wifi_credentials_t *credentials)
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(NANA_WIFI_NAMESPACE, NVS_READWRITE, &handle);
    if (result != ESP_OK) {
        return result;
    }

    result = nvs_set_str(handle, NANA_WIFI_SSID_KEY, credentials->ssid);
    if (result == ESP_OK) {
        result = nvs_set_str(
            handle, NANA_WIFI_PASSWORD_KEY, credentials->password);
    }
    if (result == ESP_OK) {
        result = nvs_commit(handle);
    }
    nvs_close(handle);
    return result;
}

static esp_err_t clear_credentials(void)
{
    nvs_handle_t handle = 0;
    esp_err_t result = nvs_open(NANA_WIFI_NAMESPACE, NVS_READWRITE, &handle);
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

static esp_err_t wait_for_initial_credentials(
    nana_wifi_credentials_t *credentials)
{
    char line[NANA_WIFI_SERIAL_LINE_BYTES] = {0};

    while (true) {
        printf("NANA_WIFI_PROVISION_READY protocol=base64-v1\n");
        fflush(stdout);
        if (!read_serial_line(line, sizeof(line), pdMS_TO_TICKS(5000))) {
            continue;
        }
        if (!parse_set_command(line, credentials)) {
            ESP_LOGW(TAG, "Ignored invalid Wi-Fi provisioning command");
            continue;
        }

        const esp_err_t result = save_credentials(credentials);
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "Wi-Fi credential save failed: %s",
                     esp_err_to_name(result));
            continue;
        }
        printf("NANA_WIFI_SAVED ssid=%s\n", credentials->ssid);
        fflush(stdout);
        return ESP_OK;
    }
}

static void serial_provisioning_task(void *argument)
{
    (void)argument;
    char line[NANA_WIFI_SERIAL_LINE_BYTES] = {0};

    while (true) {
        if (!read_serial_line(line, sizeof(line), portMAX_DELAY)) {
            continue;
        }

        if (strcmp(line, "NANA_WIFI_CLEAR") == 0) {
            const esp_err_t result = clear_credentials();
            printf("NANA_WIFI_CLEARED status=%s\n", esp_err_to_name(result));
            fflush(stdout);
            if (result == ESP_OK) {
                vTaskDelay(pdMS_TO_TICKS(100));
                esp_restart();
            }
            continue;
        }

        nana_wifi_credentials_t credentials = {0};
        if (!parse_set_command(line, &credentials)) {
            continue;
        }

        nana_wifi_credentials_t current = {0};
        if (load_credentials(&current) == ESP_OK &&
            strcmp(current.ssid, credentials.ssid) == 0 &&
            strcmp(current.password, credentials.password) == 0) {
            printf("NANA_WIFI_UNCHANGED ssid=%s\n", credentials.ssid);
            fflush(stdout);
            continue;
        }

        const esp_err_t result = save_credentials(&credentials);
        printf("NANA_WIFI_SAVED ssid=%s status=%s\n",
               credentials.ssid,
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
            "nana_wifi_config",
            3072,
            NULL,
            5,
            NULL) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    serial_listener_started = true;
    return ESP_OK;
}

static void wifi_event_handler(void *argument,
                               esp_event_base_t event_base,
                               int32_t event_id,
                               void *event_data)
{
    (void)argument;

    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        return;
    }
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        const wifi_event_sta_disconnected_t *event = event_data;
        xEventGroupClearBits(wifi_events, WIFI_CONNECTED_BIT);
        ESP_LOGW(TAG,
                 "Wi-Fi disconnected | reason=%u | retry=%d/%d",
                 event == NULL ? 0U : (unsigned)event->reason,
                 wifi_retry_count,
                 NANA_WIFI_MAX_RETRIES);
        if (wifi_reconnect_suspended) {
            return;
        }
        if (wifi_retry_count < NANA_WIFI_MAX_RETRIES) {
            ++wifi_retry_count;
            esp_wifi_connect();
        } else {
            xEventGroupSetBits(wifi_events, WIFI_FAILED_BIT);
        }
        return;
    }
    if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = event_data;
        snprintf(
            wifi_ip_address,
            sizeof(wifi_ip_address),
            IPSTR,
            IP2STR(&event->ip_info.ip));
        wifi_retry_count = 0;
        xEventGroupClearBits(wifi_events, WIFI_FAILED_BIT);
        xEventGroupSetBits(wifi_events, WIFI_CONNECTED_BIT);
    }
}

static void log_target_access_point(const char *ssid)
{
    wifi_scan_config_t scan_config = {
        .ssid = (uint8_t *)ssid,
        .show_hidden = true,
        .scan_type = WIFI_SCAN_TYPE_PASSIVE,
        .scan_time = {
            .passive = 120,
        },
    };

    const esp_err_t scan_result = esp_wifi_scan_start(&scan_config, true);
    if (scan_result != ESP_OK) {
        ESP_LOGW(TAG, "Target SSID scan failed: %s",
                 esp_err_to_name(scan_result));
        return;
    }

    uint16_t access_point_count = 0;
    if (esp_wifi_scan_get_ap_num(&access_point_count) != ESP_OK ||
        access_point_count == 0) {
        esp_wifi_clear_ap_list();
        ESP_LOGW(TAG,
                 "Target SSID is not visible on 2.4GHz channels 1-13: %s",
                 ssid);
        return;
    }

    wifi_ap_record_t record = {0};
    uint16_t record_count = 1;
    if (esp_wifi_scan_get_ap_records(&record_count, &record) == ESP_OK &&
        record_count == 1) {
        ESP_LOGI(TAG,
                 "Target SSID visible | channel=%u | rssi=%ddBm | auth=%d",
                 record.primary,
                 record.rssi,
                 record.authmode);
    }
}

static esp_err_t nana_wifi_start_internal(bool serial_provisioning_enabled)
{
    esp_err_t result = initialize_nvs();
    if (result != ESP_OK) {
        ESP_LOGE(TAG, "NVS init failed: %s", esp_err_to_name(result));
        return result;
    }

    if (serial_provisioning_enabled) {
        result = initialize_serial_input();
        if (result != ESP_OK) {
            ESP_LOGE(TAG, "Serial provisioning input failed: %s",
                     esp_err_to_name(result));
            return result;
        }
    }

    nana_wifi_credentials_t credentials = {0};
    result = load_credentials(&credentials);
    if (result != ESP_OK) {
        if (!serial_provisioning_enabled) {
            ESP_LOGE(TAG,
                     "No saved Wi-Fi configuration; provision it in camera LAN mode before starting Presence V1");
            return ESP_ERR_NOT_FOUND;
        }
        ESP_LOGW(TAG,
                 "No saved Wi-Fi configuration; waiting on USB-UART without exposing a password in source");
        result = wait_for_initial_credentials(&credentials);
        if (result != ESP_OK) {
            return result;
        }
    }

    if (serial_provisioning_enabled) {
        result = start_serial_listener();
        if (result != ESP_OK) {
            return result;
        }
    }

    wifi_events = xEventGroupCreate();
    if (wifi_events == NULL) {
        return ESP_ERR_NO_MEM;
    }

    result = esp_netif_init();
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
        return result;
    }
    result = esp_event_loop_create_default();
    if (result != ESP_OK && result != ESP_ERR_INVALID_STATE) {
        return result;
    }

    esp_netif_t *station = esp_netif_create_default_wifi_sta();
    if (station == NULL) {
        return ESP_ERR_NO_MEM;
    }
    ESP_ERROR_CHECK(esp_netif_set_hostname(station, "nana-presence"));

    const wifi_init_config_t init_config = WIFI_INIT_CONFIG_DEFAULT();
    result = esp_wifi_init(&init_config);
    if (result != ESP_OK) {
        return result;
    }

    const wifi_country_t wifi_country = {
        .cc = "VN",
        .schan = 1,
        .nchan = 13,
        .max_tx_power = NANA_WIFI_MAX_TX_POWER_QDBM,
        .policy = WIFI_COUNTRY_POLICY_MANUAL,
    };
    ESP_ERROR_CHECK(esp_wifi_set_country(&wifi_country));

    ESP_ERROR_CHECK(esp_event_handler_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, wifi_event_handler, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));

    wifi_config_t wifi_config = {0};
    strlcpy((char *)wifi_config.sta.ssid,
            credentials.ssid,
            sizeof(wifi_config.sta.ssid));
    strlcpy((char *)wifi_config.sta.password,
            credentials.password,
            sizeof(wifi_config.sta.password));
    wifi_config.sta.threshold.authmode =
        credentials.password[0] == '\0' ? WIFI_AUTH_OPEN : WIFI_AUTH_WPA2_PSK;
    wifi_config.sta.sae_pwe_h2e = WPA3_SAE_PWE_BOTH;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());
    ESP_ERROR_CHECK(esp_wifi_set_max_tx_power(NANA_WIFI_MAX_TX_POWER_QDBM));
    ESP_ERROR_CHECK(esp_wifi_set_ps(WIFI_PS_MIN_MODEM));

    log_target_access_point(credentials.ssid);
    ESP_ERROR_CHECK(esp_wifi_connect());

    ESP_LOGI(TAG,
             "Connecting to SSID=%s (password hidden) | max_tx=%.1fdBm | power_save=min_modem",
             credentials.ssid,
             (double)NANA_WIFI_MAX_TX_POWER_QDBM / 4.0);
    const EventBits_t bits = xEventGroupWaitBits(
        wifi_events,
        WIFI_CONNECTED_BIT | WIFI_FAILED_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(30000));

    if ((bits & WIFI_CONNECTED_BIT) != 0) {
        ESP_LOGI(TAG,
                 "Wi-Fi connected | ip=%s | max_tx=%.1fdBm | power_save=min_modem",
                 wifi_ip_address,
                 (double)NANA_WIFI_MAX_TX_POWER_QDBM / 4.0);
        return ESP_OK;
    }
    if ((bits & WIFI_FAILED_BIT) != 0) {
        ESP_LOGE(TAG,
                 "Wi-Fi connection failed after %d retries; send new credentials over USB-UART",
                 NANA_WIFI_MAX_RETRIES);
        return ESP_ERR_NOT_FOUND;
    }

    ESP_LOGE(TAG,
             "Wi-Fi connection timed out; serial provisioning=%s",
             serial_provisioning_enabled ? "available" : "disabled_for_voice_link");
    return ESP_ERR_TIMEOUT;
}

esp_err_t nana_wifi_start(void)
{
    return nana_wifi_start_internal(true);
}

esp_err_t nana_wifi_start_runtime(void)
{
    return nana_wifi_start_internal(false);
}

esp_err_t nana_wifi_interrupt_for_test(uint32_t duration_ms)
{
    if (duration_ms < 250U || duration_ms > 10000U) {
        return ESP_ERR_INVALID_ARG;
    }

    ESP_LOGW(TAG, "Maintenance Wi-Fi interruption begins | duration=%" PRIu32 "ms",
             duration_ms);
    if (wifi_events != NULL) {
        xEventGroupClearBits(
            wifi_events, WIFI_CONNECTED_BIT | WIFI_FAILED_BIT);
    }

    wifi_reconnect_suspended = true;
    wifi_retry_count = 0;
    esp_err_t result = esp_wifi_stop();
    if (result != ESP_OK) {
        wifi_reconnect_suspended = false;
        return result;
    }
    vTaskDelay(pdMS_TO_TICKS(duration_ms));

    result = esp_wifi_start();
    wifi_reconnect_suspended = false;
    if (result == ESP_OK) {
        result = esp_wifi_connect();
    }
    ESP_LOGW(TAG, "Maintenance Wi-Fi interruption ends | status=%s",
             esp_err_to_name(result));
    return result;
}

esp_err_t nana_wifi_wait_connected(uint32_t timeout_ms)
{
    if (wifi_events == NULL) {
        return ESP_ERR_INVALID_STATE;
    }

    const EventBits_t bits = xEventGroupWaitBits(
        wifi_events,
        WIFI_CONNECTED_BIT | WIFI_FAILED_BIT,
        pdFALSE,
        pdFALSE,
        pdMS_TO_TICKS(timeout_ms));
    if ((bits & WIFI_CONNECTED_BIT) != 0U) {
        return ESP_OK;
    }
    if ((bits & WIFI_FAILED_BIT) != 0U) {
        return ESP_FAIL;
    }
    return ESP_ERR_TIMEOUT;
}

const char *nana_wifi_ip_address(void)
{
    return wifi_ip_address;
}
