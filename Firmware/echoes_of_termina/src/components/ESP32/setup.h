#if !defined(SETUP_ESP32)
#define SETUP_ESP32

#include "shared/dependencies.h"
#include "components/ESP32/features/update_firmware_ota.h"
#include "driver/adc.h"
#include "esp_adc_cal.h"
#include "esp_heap_caps.h"
#include "components/ESP32/features/i2s_output_test.h"

esp_adc_cal_characteristics_t adc_cal;

// ── PSRAM: on or off, decided by the selected pinout ────────────────────────
// The module's 2 MB PSRAM chip is wired internally to GPIO16 (CS) and GPIO17
// (CLK). That is harmless when the board leaves those pins alone — Marajoara_V3
// routes its I2S to 18/25/26/34 — and fatal when a pinout claims them for I2S:
// the bit clock on GPIO17 becomes the PSRAM clock and every external RAM access
// comes back as garbage. That is exactly what the boot probes caught on Echoes
// V1: heap state healthy (TLSF handle 3F800014, 2045 KB free) right up to
// a2dp_sink.start(), then a garbage heap handle (0xFFFFFFFF) at bluedroid's
// first >4 KB malloc, i.e. inside BTE_InitStack.
//
// The board file's -DBOARD_HAS_PSRAM does NOT decide this: the prebuilt
// Arduino-ESP32 libraries always have CONFIG_SPIRAM=y, so initArduino() runs
// psramInit() on every ESP32 build. The core's only escape hatch is this weak
// hook (esp32-hal-psram.c line 42): returning false makes psramInit() bail out
// before it registers the external heap and before it enables "prefer external
// RAM above 4 KB". Where the pinout leaves GPIO16/17 alone we want stock
// behaviour, so the override only exists for the conflicting boards.
#if !PINOUT_PSRAM_AVAILABLE
extern "C" bool testSPIRAM(void) { return false; }
#endif

void setup_ESP32()
{
    Serial.println("[ESP32] SETUP STARTED!");

#if !PINOUT_PSRAM_AVAILABLE
    //* Belt and braces: with testSPIRAM() overridden, psramInit() never reaches
    //* heap_caps_malloc_extmem_enable(4096), so malloc already prefers internal
    //* RAM. Setting it explicitly means a >4 KB allocation can never be routed
    //* to the (unused) external heap by some other code path either.
    //* (size_t)-1 == MALLOC_DISABLE_EXTERNAL_ALLOCS.
    heap_caps_malloc_extmem_enable((size_t)-1);
#endif

    Serial.printf("[PINOUT] %s — PSRAM %s\n", PINOUT_NAME,
                  PINOUT_PSRAM_AVAILABLE ? "enabled" : "disabled");

    //* Heap census — deliberately only calls that never dereference a TLSF
    //* control block, so this stays safe even if a heap handle is corrupt.
    Serial.printf("[HEAP] malloc-visible %u KB | internal %u KB\n",
                  (unsigned)(heap_caps_get_free_size(MALLOC_CAP_DEFAULT) / 1024),
                  (unsigned)(heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
    Serial.printf("[HEAP] spiram free %u KB of %u KB | psramFound=%d psramSize=%u KB\n",
                  (unsigned)(heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024),
                  (unsigned)(heap_caps_get_total_size(MALLOC_CAP_SPIRAM) / 1024),
                  (int)psramFound(),
                  (unsigned)(ESP.getPsramSize() / 1024));

    //* Setup Watchdog
    esp_task_wdt_init(180, true);
    esp_task_wdt_add(NULL);

    //* OTA is NOT started here. This function runs before any WiFi init, and
    //* ArduinoOTA::begin() creates a lwIP socket, which asserts ("Invalid mbox")
    //* until esp_netif_init() has run — i.e. until WiFi.mode()/WiFi.begin().
    //* It is started from setup_wifi_mode(), once the board is actually
    //* connected. See components/ESP32/features/update_firmware_ota.h.

    adc1_config_width(ADC_WIDTH_BIT_12);
    adc1_config_channel_atten(ADC1_CHANNEL_0, ADC_ATTEN_DB_12);
    esp_adc_cal_characterize(ADC_UNIT_1, ADC_ATTEN_DB_12, ADC_WIDTH_BIT_12, 1100, &adc_cal);

    Serial.println("[ESP32] SETUP FINISHED!");
}

void setup_GPIOs()
{
    Serial.println("[GPIO] SETUP STARTED!");

    // prog_switch (GPIO 0) is configured in setup_button_interrupt()

    Serial.println("[GPIO] SETUP FINISHED!");
}

#endif // SETUP_ESP32