// H4 USB-UART bridge to ESP32's Bluetooth controller.
// Audio encoding and A2DP host run on the PC. No Windows kernel driver needed.
#include <Arduino.h>
#include "driver/uart.h"
#include "esp_bt.h"
#include "esp32-hal-bt.h"
#include "esp_log.h"
#include "status_led.h"

// GPIO2 is the user LED verified with the original blink firmware.
static constexpr int LED_PIN = 2;
static constexpr uint16_t LED_OPCODE = 0xFCF0;
static portMUX_TYPE led_lock = portMUX_INITIALIZER_UNLOCKED;
static LedState led_state = LedState::Idle;
static uint32_t led_started = 0;
static uint32_t led_heartbeat = 0;
static bool led_error = false;
static uint32_t led_error_started = 0;

static void update_led(LedState state) {
    uint32_t now = millis();
    portENTER_CRITICAL(&led_lock);
    led_heartbeat = now;
    if (state == LedState::Error) {
        if (!led_error) led_error_started = now;
        led_error = true;
    } else if (state != led_state) {
        led_state = state;
        led_started = now;
    }
    portEXIT_CRITICAL(&led_lock);
}

static void led_task(void *) {
    for (;;) {
        uint32_t now = millis();
        portENTER_CRITICAL(&led_lock);
        if (led_error && uint32_t(now - led_error_started) >= 5000) led_error = false;
        if (led_state != LedState::Idle && uint32_t(now - led_heartbeat) >= 8000) {
            led_state = LedState::Idle;
            led_started = now;
            if (!led_error) led_error_started = now;
            led_error = true;
        }
        LedState state = led_error ? LedState::Error : led_state;
        uint32_t elapsed = now - (led_error ? led_error_started : led_started);
        portEXIT_CRITICAL(&led_lock);
        digitalWrite(LED_PIN, led_on(state, elapsed) ? HIGH : LOW);
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}

static bool handle_led_command(const uint8_t *packet, size_t length) {
    if (packet[0] != 1 || (packet[1] | (packet[2] << 8)) != LED_OPCODE) return false;
    if (length == 8 && packet[3] == 4 && memcmp(packet + 4, "LED?", 4) == 0) {
        portENTER_CRITICAL(&led_lock);
        LedState state = led_error ? LedState::Error : led_state;
        uint32_t elapsed = millis() - (led_error ? led_error_started : led_started);
        portEXIT_CRITICAL(&led_lock);
        const uint8_t reply[] = {4, 0x0E, 13, 1, 0xF0, 0xFC, 0, 'L', 'E', 'D', 'S', 1,
                                uint8_t(state), uint8_t(digitalRead(LED_PIN)),
                                uint8_t(elapsed), uint8_t(elapsed >> 8)};
        uart_write_bytes(UART_NUM_0, reinterpret_cast<const char *>(reply), sizeof(reply));
        return true;
    }
    // Vendor command is local to this bridge. Never forward it to the controller.
    bool valid = length == 9 && packet[3] == 5 &&
                 memcmp(packet + 4, "LEDS", 4) == 0 && packet[8] <= 6;
    if (valid) update_led(static_cast<LedState>(packet[8]));
    const uint8_t reply[] = {4, 0x0E, 9, 1, 0xF0, 0xFC,
                            uint8_t(valid ? 0 : 0x12), 'L', 'E', 'D', 'S', 1};
    uart_write_bytes(UART_NUM_0, reinterpret_cast<const char *>(reply), sizeof(reply));
    return true;
}

static volatile bool ready = false;
static void can_send() { ready = true; }
static int received(uint8_t *data, uint16_t length) {
    uart_write_bytes(UART_NUM_0, (const char *)data, length);
    return 0;
}
static esp_vhci_host_callback_t callbacks = {can_send, received};

void setup() {
    esp_log_level_set("*", ESP_LOG_NONE);
    pinMode(LED_PIN, OUTPUT);
    digitalWrite(LED_PIN, LOW);
    xTaskCreate(led_task, "status_led", 2048, nullptr, 1, nullptr);
    uart_config_t cfg = {};
    cfg.baud_rate = 2000000;
    cfg.data_bits = UART_DATA_8_BITS;
    cfg.parity = UART_PARITY_DISABLE;
    cfg.stop_bits = UART_STOP_BITS_1;
    cfg.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
    cfg.source_clk = UART_SCLK_APB;
    ESP_ERROR_CHECK(uart_param_config(UART_NUM_0, &cfg));
    ESP_ERROR_CHECK(uart_set_pin(UART_NUM_0, 1, 3, UART_PIN_NO_CHANGE, UART_PIN_NO_CHANGE));
    ESP_ERROR_CHECK(uart_driver_install(UART_NUM_0, 16384, 16384, 0, nullptr, 0));
    // Referencing Arduino's BT HAL also retains its btInUse() implementation;
    // otherwise initArduino() releases controller memory before setup().
    if (!btStart()) { esp_restart(); return; }
    ESP_ERROR_CHECK(esp_vhci_host_register_callback(&callbacks));
}

static bool read_exact(uint8_t *p, size_t n) {
    while (n) {
        int count = uart_read_bytes(UART_NUM_0, p, n, pdMS_TO_TICKS(1000));
        if (count <= 0) return false;
        p += count;
        n -= count;
    }
    return true;
}

void loop() {
    static uint8_t packet[4096];
    if (!read_exact(packet, 1)) return;
    size_t header;
    if (packet[0] == 1) header = 3;       // HCI command
    else if (packet[0] == 2) header = 4;  // ACL
    else return;
    if (!read_exact(packet + 1, header)) return;
    size_t length = packet[0] == 1 ? packet[3] : (packet[3] | (packet[4] << 8));
    if (length + header + 1 > sizeof(packet)) { esp_restart(); return; }
    if (!read_exact(packet + 1 + header, length)) return;
    if (handle_led_command(packet, length + header + 1)) return;
    while (!esp_vhci_host_check_send_available()) vTaskDelay(1);
    esp_vhci_host_send_packet(packet, length + header + 1);
}
