// H4 USB-UART bridge to ESP32's Bluetooth controller.
// Audio encoding and A2DP host run on the PC. No Windows kernel driver needed.
#include <Arduino.h>
#include "driver/uart.h"
#include "esp_bt.h"
#include "esp32-hal-bt.h"
#include "esp_log.h"

static volatile bool ready = false;
static void can_send() { ready = true; }
static int received(uint8_t *data, uint16_t length) {
    uart_write_bytes(UART_NUM_0, (const char *)data, length);
    return 0;
}
static esp_vhci_host_callback_t callbacks = {can_send, received};

void setup() {
    esp_log_level_set("*", ESP_LOG_NONE);
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
    while (!esp_vhci_host_check_send_available()) vTaskDelay(1);
    esp_vhci_host_send_packet(packet, length + header + 1);
}
